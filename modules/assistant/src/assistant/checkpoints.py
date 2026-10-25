"""
Deletes an agent's checkpoints of a conversation: what DynamoDBSaver
(langgraph-checkpoint-aws) wrote for its thread, in the checkpoint table and,
for a checkpoint over 350 KB, in its bucket.

The purge worker (purge.py) is a Lambda with the shared layer only, so this
uses boto3 rather than the library, which needs LangGraph. It follows the
library's layout (checkpoint/dynamodb/unified_repository.py):

  PK = CHECKPOINT_<thread>                    SK = <ns>#<checkpoint id>   one per checkpoint
  PK = WRITES_<thread>#<ns>#<checkpoint id>   SK = <task id>#<idx>        that checkpoint's writes
  PK = <ref_key>                              SK = CHUNK                  a payload kept in the table
  s3://<bucket>/<thread>/...                                              a payload kept in S3

Checkpoints and writes name their payload (ref_key, ref_loc). The layout is
the library's own, so a test writes real checkpoints with DynamoDBSaver and
checks this leaves nothing.
"""
from boto3.dynamodb.conditions import Key

S3_DELETE_LIMIT = 1000   # keys per DeleteObjects call


class CheckpointPurge:
    def __init__(self, table, s3, bucket: str):
        self.table = table     # boto3 "dynamodb" Table: the checkpoint table
        self.s3 = s3           # boto3 "s3"
        self.bucket = bucket

    def delete(self, thread_id: str) -> int:
        """
        Deletes every record of this thread, and returns how many. The
        checkpoints go last: they lead to the rest, so a run that fails half
        way is retried and finishes the job.
        """
        checkpoints = self._query(f"CHECKPOINT_{thread_id}")
        writes = [item for checkpoint in checkpoints for item in self._query(f"WRITES_{thread_id}#{checkpoint['SK']}")]
        payloads = [{"PK": item["ref_key"], "SK": "CHUNK"} for item in checkpoints + writes
                    if item.get("ref_loc") == "DYNAMODB"]
        self._delete_objects(f"{thread_id}/")
        keys = payloads + [{"PK": item["PK"], "SK": item["SK"]} for item in writes + checkpoints]
        with self.table.batch_writer() as batch:   # 25 at a time, in order, retrying what DynamoDB left
            for key in keys:
                batch.delete_item(Key=key)
        return len(keys)

    def _query(self, pk: str) -> list[dict]:
        items, params = [], {"KeyConditionExpression": Key("PK").eq(pk),
                             "ProjectionExpression": "PK, SK, ref_key, ref_loc"}
        while True:
            page = self.table.query(**params)
            items += page["Items"]
            if "LastEvaluatedKey" not in page:
                return items
            params["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def _delete_objects(self, prefix: str) -> None:
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            for start in range(0, len(keys), S3_DELETE_LIMIT):
                self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": keys[start:start + S3_DELETE_LIMIT]})
