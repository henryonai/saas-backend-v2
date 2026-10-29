"""
The only code that touches the accounts table. The table is injected
(not looked up from a global), so tests pass a fake one.

Key design:
  pk                  sk                  what
  TENANT#<tenant_id>  PROFILE             tenant (seats_used = members). While it is deleted
                                          (status deleting or purging): gsi1 DELETING /
                                          <delete_after>#<tenant_id>, which the purge job reads
  TENANT#<tenant_id>  USER#<user_id>      member
  TENANT#<tenant_id>  INVITE#<email>      invitation (gsi1: EMAIL#<email>; removed by TTL when expired)
  TENANT#<tenant_id>  INVITES_SENT#<day>  invitations sent that day (rate limit, expires)
  USER#<user_id>      WORKSPACE#<tenant>  one of the user's workspaces (one item per membership)
  USER#<user_id>      WORKSPACES          how many workspaces the user belongs to (count, limited)
  USER#<user_id>      IDEMPOTENCY#<user_id>#<key>  the workspace a POST /tenants with that key created
                                          (shared/idempotency.py; expires)
  (A deleted account's USER# items go, except the memberships of workspaces it
  owned that wait to be purged: the purge job removes those.)
  OUTBOX#<event_id>   OUTBOX              events to publish (shared/outbox.py)

Rules that must hold even under concurrent requests (the workspaces per user,
the seat limit, owner protection) are DynamoDB conditions, not "read, then
write" code.
"""
import time

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from shared.dynamo import ConditionFailed, delete_partition, is_condition_failure, transact
from shared.events import Event
from shared.idempotency import idempotency_put, replay
from shared.outbox import outbox_put

from accounts.models import Invitation, Member, Tenant

KEY_FIELDS = ("pk", "sk", "gsi1pk", "gsi1sk")
DAY_SECONDS = 24 * 60 * 60
DELETING = "DELETING"   # gsi1pk of workspaces waiting to be purged (a sparse index: only those)


def _clean(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in KEY_FIELDS}


def _tenant_pk(tenant_id: str) -> str:
    return f"TENANT#{tenant_id}"


def _profile(tenant_id: str) -> dict:
    return {"pk": _tenant_pk(tenant_id), "sk": "PROFILE"}


def _member_key(tenant_id: str, user_id: str) -> dict:
    return {"pk": _tenant_pk(tenant_id), "sk": f"USER#{user_id}"}


def _workspace_key(user_id: str, tenant_id: str) -> dict:
    return {"pk": f"USER#{user_id}", "sk": f"WORKSPACE#{tenant_id}"}


def _workspace_count_key(user_id: str) -> dict:
    return {"pk": f"USER#{user_id}", "sk": "WORKSPACES"}


def _invite_key(tenant_id: str, email: str) -> dict:
    return {"pk": _tenant_pk(tenant_id), "sk": f"INVITE#{email}"}


def _join(m: Member, max_workspaces: int) -> dict:
    """
    The two operations that add a workspace to the user's list: "membership"
    fails if they are in it already, "workspaces" if they are in max_workspaces.
    """
    return {
        "membership": {"Put": {"Item": {**_workspace_key(m.user_id, m.tenant_id), "tenant_id": m.tenant_id},
                               "ConditionExpression": "attribute_not_exists(pk)"}},
        "workspaces": {"Update": {
            "Key": _workspace_count_key(m.user_id),
            "UpdateExpression": "ADD #count :one",
            "ConditionExpression": "attribute_not_exists(#count) OR #count < :max",
            "ExpressionAttributeNames": {"#count": "count"},
            "ExpressionAttributeValues": {":one": 1, ":max": max_workspaces},
        }},
    }


def _leave(user_id: str, tenant_id: str) -> dict:
    return {
        "membership": {"Delete": {"Key": _workspace_key(user_id, tenant_id)}},
        "workspaces": {"Update": {
            "Key": _workspace_count_key(user_id),
            "UpdateExpression": "ADD #count :minus_one",
            "ExpressionAttributeNames": {"#count": "count"},
            "ExpressionAttributeValues": {":minus_one": -1},
        }},
    }


def _seats(tenant_id: str, change: int, max_seats: int | None = None) -> dict:
    update = {
        "Key": _profile(tenant_id),
        "UpdateExpression": "ADD seats_used :change",
        "ConditionExpression": "attribute_exists(pk)",
        "ExpressionAttributeValues": {":change": change},
    }
    if max_seats is not None:
        update["ConditionExpression"] += " AND (attribute_not_exists(seats_used) OR seats_used < :max)"
        update["ExpressionAttributeValues"][":max"] = max_seats
    return {"Update": update}


class AccountRepository:
    def __init__(self, table):
        self.table = table

    # ---------- tenants ----------

    def create_tenant(self, tenant: Tenant, owner: Member, max_workspaces: int,
                      idempotency: tuple[str, str]) -> None:
        """
        Raises ConditionFailed({"workspaces"}) if the owner is in max_workspaces already.
        `idempotency` is the client's (key, request fingerprint), saved under the
        user (the workspace is new): a retry with the key raises
        ConditionFailed({IDEMPOTENCY, ...}) and changes nothing (see tenant_for_key).
        """
        transact(
            self.table,
            **_join(owner, max_workspaces),
            tenant={"Put": {"Item": {**_profile(tenant.tenant_id), **tenant.model_dump()}}},
            member={"Put": {"Item": self._member_item(owner)}},
            idempotency=idempotency_put(f"USER#{owner.user_id}", owner.user_id, *idempotency, tenant.tenant_id),
        )

    def tenant_for_key(self, user_id: str, key: str, request: str) -> str:
        """The id of the workspace the first create with this key made (shared.idempotency.replay)."""
        return replay(self.table, f"USER#{user_id}", user_id, key, request)

    def get_tenant(self, tenant_id: str, consistent: bool = False) -> Tenant | None:
        """`consistent`: right after it was created (a create's retry)."""
        item = self.table.get_item(Key=_profile(tenant_id), ConsistentRead=consistent).get("Item")
        return Tenant.model_validate(_clean(item)) if item else None

    def apply_subscription(self, tenant_id: str, plan: str, status: str, revision: int) -> bool:
        """Updates the plan copy unless it already has this or a newer revision. False if skipped."""
        try:
            self.table.update_item(
                Key=_profile(tenant_id),
                UpdateExpression="SET #p = :p, subscription_status = :s, subscription_revision = :r",
                ConditionExpression=("attribute_exists(pk) AND (attribute_not_exists(subscription_revision)"
                                     " OR subscription_revision < :r)"),
                ExpressionAttributeNames={"#p": "plan"},
                ExpressionAttributeValues={":p": plan, ":s": status, ":r": revision},
            )
            return True
        except ClientError as e:
            if is_condition_failure(e):
                return False
            raise

    # ---------- deleting a workspace ----------

    def schedule_deletion(self, tenant_id: str, delete_after: int, event: Event) -> bool:
        """Marks an active workspace deleting until delete_after, with its event. False if it isn't active."""
        return self._change(event, {"Update": {
            "Key": _profile(tenant_id),
            "UpdateExpression": "SET #st = :deleting, delete_after = :at, gsi1pk = :index, gsi1sk = :due",
            "ConditionExpression": "attribute_exists(pk) AND (attribute_not_exists(#st) OR #st = :active)",
            "ExpressionAttributeNames": {"#st": "status"},
            "ExpressionAttributeValues": {":deleting": "deleting", ":active": "active", ":at": delete_after,
                                          ":index": DELETING, ":due": f"{delete_after:012d}#{tenant_id}"},
        }})

    def restore(self, tenant_id: str, now: int, event: Event) -> bool:
        """
        Makes a deleting workspace active again, with its event. False if it
        isn't deleting (or already purging), or its delete_after has passed.
        """
        return self._change(event, {"Update": {
            "Key": _profile(tenant_id),
            "UpdateExpression": "SET #st = :active REMOVE delete_after, gsi1pk, gsi1sk",
            "ConditionExpression": "#st = :deleting AND delete_after > :now",
            "ExpressionAttributeNames": {"#st": "status"},
            "ExpressionAttributeValues": {":deleting": "deleting", ":active": "active", ":now": now},
        }})

    def due_for_purge(self, now: int) -> list[str]:
        """Workspaces whose grace period has ended (and ones a purge left unfinished), oldest first."""
        ids, start = [], {}
        while True:
            result = self.table.query(
                IndexName="gsi1",
                KeyConditionExpression=Key("gsi1pk").eq(DELETING) & Key("gsi1sk").lte(f"{now:012d}~"),
                **start,
            )
            ids += [item["gsi1sk"].split("#", 1)[1] for item in result["Items"]]
            if "LastEvaluatedKey" not in result:
                return ids
            start = {"ExclusiveStartKey": result["LastEvaluatedKey"]}

    def start_purge(self, tenant_id: str, event: Event) -> None:
        """
        deleting -> purging, and saves TenantDeleted, in one transaction: the
        event is published once, however often the purge is retried. Raises
        ConditionFailed({"tenant"}) if it isn't deleting (restored, or purging already).
        """
        transact(
            self.table,
            tenant={"Update": {
                "Key": _profile(tenant_id),
                "UpdateExpression": "SET #st = :purging",
                "ConditionExpression": "#st = :deleting",
                "ExpressionAttributeNames": {"#st": "status"},
                "ExpressionAttributeValues": {":deleting": "deleting", ":purging": "purging"},
            }},
            event=outbox_put(event),
        )

    def purge_member(self, tenant_id: str, user_id: str) -> bool:
        """Removes a member of a workspace being purged, and it from their list. False if gone already."""
        leave = _leave(user_id, tenant_id)
        if "Item" not in self.table.get_item(Key=_workspace_count_key(user_id)):
            leave["workspaces"] = None   # a deleted account: nothing to count any more
        try:
            transact(
                self.table,
                member={"Delete": {"Key": _member_key(tenant_id, user_id),
                                   "ConditionExpression": "attribute_exists(pk)"}},
                **leave,
            )
            return True
        except ConditionFailed:
            return False

    def purge_rest(self, tenant_id: str) -> int:
        """
        Deletes the workspace's remaining items (invitations, counters), then
        its profile, which takes it out of the purge job's index. Returns how many.
        """
        deleted = delete_partition(self.table, _tenant_pk(tenant_id), keep=lambda sk: sk == "PROFILE")
        self.table.delete_item(Key=_profile(tenant_id))
        return deleted + 1

    def delete_user(self, user_id: str, event: Event) -> int:
        """
        A deleted account: saves UserDeleted, then deletes the user's own items
        (workspace count, keys), keeping memberships the purge job still
        removes. Safe to repeat. Returns how many items went.
        """
        transact(self.table, event=outbox_put(event))
        return delete_partition(self.table, f"USER#{user_id}", keep=lambda sk: sk.startswith("WORKSPACE#"))

    # ---------- members ----------

    def workspaces_of(self, user_id: str) -> list[tuple[Tenant, Member]]:
        """
        The user's workspaces, each with their membership there. Strongly
        consistent: right after creating or joining one, the token refresh sees it.
        """
        result = self.table.query(
            KeyConditionExpression=Key("pk").eq(f"USER#{user_id}") & Key("sk").begins_with("WORKSPACE#"),
            ConsistentRead=True,
        )
        tenant_ids = [item["tenant_id"] for item in result["Items"]]
        items = self._get_many([key for t in tenant_ids for key in (_profile(t), _member_key(t, user_id))])

        def found(key: dict) -> dict | None:
            return items.get((key["pk"], key["sk"]))

        workspaces = []
        for t in tenant_ids:
            profile, member = found(_profile(t)), found(_member_key(t, user_id))
            if profile and member:
                workspaces.append((Tenant.model_validate(_clean(profile)), Member.model_validate(_clean(member))))
        return workspaces

    def get_member(self, tenant_id: str, user_id: str) -> Member | None:
        item = self.table.get_item(Key=_member_key(tenant_id, user_id), ConsistentRead=True).get("Item")
        return Member.model_validate(_clean(item)) if item else None

    def list_members(self, tenant_id: str) -> list[Member]:
        result = self.table.query(
            KeyConditionExpression=Key("pk").eq(_tenant_pk(tenant_id)) & Key("sk").begins_with("USER#")
        )
        return [Member.model_validate(_clean(i)) for i in result["Items"]]

    def change_role(self, tenant_id: str, user_id: str, role: str) -> bool:
        """False if the member no longer exists or is the owner."""
        try:
            self.table.update_item(
                Key=_member_key(tenant_id, user_id),
                UpdateExpression="SET #r = :r",
                ConditionExpression="attribute_exists(pk) AND #r <> :owner",
                ExpressionAttributeNames={"#r": "role"},
                ExpressionAttributeValues={":r": role, ":owner": "owner"},
            )
            return True
        except ClientError as e:
            if is_condition_failure(e):
                return False
            raise

    def remove_member(self, tenant_id: str, user_id: str, event: Event) -> bool:
        """
        Removes the member, frees their seat and saves the event, in one
        transaction. False if not found (or the owner): then no event.
        """
        try:
            transact(
                self.table,
                member={"Delete": {
                    "Key": _member_key(tenant_id, user_id),
                    "ConditionExpression": "attribute_exists(pk) AND #r <> :owner",
                    "ExpressionAttributeNames": {"#r": "role"},
                    "ExpressionAttributeValues": {":owner": "owner"},
                }},
                **_leave(user_id, tenant_id),
                seats=_seats(tenant_id, -1),
                event=outbox_put(event),
            )
            return True
        except ConditionFailed:
            return False

    # ---------- invitations ----------

    def invite(self, invitation: Invitation, event: Event, invites_per_day: int) -> None:
        """
        Saves the invitation and its event, and counts it against today's
        invite limit, in one transaction. Raises ConditionFailed with
        "invitation" (already invited) or "daily_limit".
        """
        day = time.strftime("%Y-%m-%d", time.gmtime())
        transact(
            self.table,
            invitation={"Put": {
                "Item": {**_invite_key(invitation.tenant_id, invitation.email),
                         "gsi1pk": f"EMAIL#{invitation.email}",
                         "gsi1sk": f"{invitation.created_at}#{invitation.tenant_id}",
                         **invitation.model_dump()},
                # An expired invitation (not yet removed by TTL) may be replaced.
                "ConditionExpression": "attribute_not_exists(pk) OR expires_at <= :now",
                "ExpressionAttributeValues": {":now": int(time.time())},
            }},
            daily_limit={"Update": {
                "Key": {"pk": _tenant_pk(invitation.tenant_id), "sk": f"INVITES_SENT#{day}"},
                "UpdateExpression": "ADD sent :one SET expires_at = :expires",
                "ConditionExpression": "attribute_not_exists(sent) OR sent < :limit",
                "ExpressionAttributeValues": {":one": 1, ":limit": invites_per_day,
                                              ":expires": int(time.time()) + 2 * DAY_SECONDS},
            }},
            event=outbox_put(event),
        )

    def get_invitation(self, tenant_id: str, email: str) -> Invitation | None:
        item = self.table.get_item(Key=_invite_key(tenant_id, email), ConsistentRead=True).get("Item")
        return Invitation.model_validate(_clean(item)) if item else None

    def list_invitations(self, tenant_id: str) -> list[Invitation]:
        result = self.table.query(
            KeyConditionExpression=Key("pk").eq(_tenant_pk(tenant_id)) & Key("sk").begins_with("INVITE#")
        )
        return [Invitation.model_validate(_clean(i)) for i in result["Items"]]

    def invitations_for(self, email: str) -> list[Invitation]:
        """Invitations sent to this email by any workspace, oldest first."""
        result = self.table.query(IndexName="gsi1", KeyConditionExpression=Key("gsi1pk").eq(f"EMAIL#{email}"))
        return [Invitation.model_validate(_clean(i)) for i in result["Items"]]

    def delete_invitation(self, tenant_id: str, email: str) -> bool:
        """False if there is no such invitation."""
        try:
            self.table.delete_item(Key=_invite_key(tenant_id, email), ConditionExpression="attribute_exists(pk)")
            return True
        except ClientError as e:
            if is_condition_failure(e):
                return False
            raise

    def accept_invitation(self, invitation: Invitation, member: Member, max_seats: int | None,
                          max_workspaces: int) -> None:
        """
        Turns the invitation into a membership and takes a seat, in one
        transaction. Raises ConditionFailed with "membership" (already in this
        workspace), "workspaces" (in max_workspaces already), "invitation"
        (cancelled or expired) or "seats" (full).
        """
        transact(
            self.table,
            **_join(member, max_workspaces),
            member={"Put": {"Item": self._member_item(member)}},
            invitation={"Delete": {
                "Key": _invite_key(invitation.tenant_id, invitation.email),
                "ConditionExpression": "attribute_exists(pk) AND expires_at > :now",
                "ExpressionAttributeValues": {":now": int(time.time())},
            }},
            seats=_seats(invitation.tenant_id, +1, max_seats),
        )

    # ---------- helpers ----------

    def _change(self, event: Event | None, op: dict) -> bool:
        """One conditional write and its event, in one transaction. False if the condition failed."""
        try:
            transact(self.table, change=op, event=outbox_put(event) if event else None)
            return True
        except ConditionFailed:
            return False

    @staticmethod
    def _member_item(m: Member) -> dict:
        return {**_member_key(m.tenant_id, m.user_id), **m.model_dump()}

    def _get_many(self, keys: list[dict]) -> dict[tuple[str, str], dict]:
        """Strongly consistent reads of up to 100 items, by (pk, sk)."""
        found: dict[tuple[str, str], dict] = {}
        request = {self.table.name: {"Keys": keys, "ConsistentRead": True}} if keys else {}
        while request:
            result = self.table.meta.client.batch_get_item(RequestItems=request)
            for item in result["Responses"].get(self.table.name, []):
                found[(item["pk"], item["sk"])] = item
            request = result.get("UnprocessedKeys") or {}   # DynamoDB may return some for later
        return found
