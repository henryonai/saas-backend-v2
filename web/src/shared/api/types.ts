/**
 * The HTTP API's request and response shapes, from the generated OpenAPI types
 * (`pnpm gen:api`). The documents describe inputs, so fields with a default
 * (ids, timestamps) come out optional; Pydantic always sends them in a
 * response, hence `Sent<...>`.
 */
import type { Message } from '@ag-ui/client';
import type { components as Accounts } from './generated/accounts';
import type { components as Admin } from './generated/admin';
import type { components as Assistant } from './generated/assistant';
import type { components as Audit } from './generated/audit';
import type { components as Billing } from './generated/billing';
import type { components as Notifications } from './generated/notifications';
import type { components as Orders } from './generated/orders';

type Sent<T> = Required<T>;

export type Tenant = Sent<Accounts['schemas']['TenantOut']>;
/** One of the signed-in user's workspaces, with their role there (GET /tenants). */
export type WorkspaceSummary = Accounts['schemas']['WorkspaceOut'];
export type TenantCreate = Accounts['schemas']['TenantCreate'];
export type Member = Sent<Accounts['schemas']['Member']>;
export type Invitation = Sent<Accounts['schemas']['Invitation']>;
export type InviteRequest = Accounts['schemas']['InviteRequest'];
export type RoleUpdate = Accounts['schemas']['RoleUpdate'];
/** A copy of a workspace's data, or of the user's own; `files` links each part once it is done. */
export type DataExport = Sent<Accounts['schemas']['ExportOut']>;

/** Who did what in the workspace (GET /audit). `details` are the event's own fields. */
export type AuditEntry = Sent<Audit['schemas']['AuditEntry']>;
export type AuditPage = { items: AuditEntry[]; next_cursor: string | null };

/** The back-office's view of a workspace, and what platform admins did (/admin). */
export type AdminWorkspace = Sent<Admin['schemas']['WorkspaceSummary']>;
export type AdminWorkspacePage = { items: AdminWorkspace[]; next_cursor: string | null };
export type AdminAction = Sent<Admin['schemas']['AdminAction']>;
export type AdminLogPage = { items: AdminAction[]; next_cursor: string | null };

export type Order = Sent<Orders['schemas']['Order']>;
export type OrderStatus = Orders['schemas']['OrderStatus'];
export type OrderCreate = Orders['schemas']['OrderCreate'];
export type OrderPage = { items: Order[]; next_cursor: string | null };

export type Notification = Sent<Notifications['schemas']['Notification']>;

export type BillingSummary = Billing['schemas']['BillingSummary'];
export type PaidPlan = Billing['schemas']['CheckoutRequest']['plan'];
export type CheckoutResponse = Billing['schemas']['CheckoutResponse'];
export type PortalResponse = Billing['schemas']['PortalResponse'];

/** One of the user's conversations with the assistant; thread_id is its AG-UI threadId. */
export type AssistantConversation = Sent<Assistant['schemas']['ConversationOut']>;
export type AssistantConversationPage = { items: AssistantConversation[]; next_cursor: string | null };
/** The API sends the messages as plain objects: they are AG-UI messages, as @ag-ui/client assembles them. */
export type AssistantConversationDetail = Omit<Sent<Assistant['schemas']['ConversationDetail']>, 'messages'> & { messages: Message[] };
/** Messages before the ones a page has, oldest first; `earlier_cursor` reads further back (null: none left). */
export type AssistantMessagePage = Omit<Sent<Assistant['schemas']['MessagePage']>, 'messages'> & { messages: Message[] };
/** A sent message's run: AgentCore streams its answer (the AG-UI runId). */
export type AssistantRun = Sent<Assistant['schemas']['RunOut']>;
/** Which version of a message one is, of how many (an edit, or an answer asked for again): "‹ 2/3 ›". */
export type AssistantVersion = Assistant['schemas']['Version'];
/** Something the assistant remembers about the user across conversations; thread_id: where an agent saved it. */
export type AssistantMemory = Sent<Assistant['schemas']['MemoryOut']>;
/** Everything it remembers about the user, and whether memory is on. */
export type AssistantMemoryList = Omit<Assistant['schemas']['MemoryList'], 'items'> & { items: AssistantMemory[] };
/** One of the user's files: one they attached (source upload) or one an agent made (assistant), with its versions. */
export type AssistantFile = Sent<Assistant['schemas']['FileOut']>;
export type AssistantFileKind = AssistantFile['kind'];
/** A file with short-lived links: to download it, and, an image, to show it. */
export type AssistantFileDetail = Sent<Assistant['schemas']['FileDetail']>;
export type AssistantFilePage = { items: AssistantFile[]; next_cursor: string | null };
/** Where the browser uploads a file to attach: a presigned POST to S3. */
export type AssistantUpload = Omit<Assistant['schemas']['UploadOut'], 'file'> & { file: AssistantFile };
/** The user's thumbs up or down on an answer. */
export type AssistantRating = NonNullable<Assistant['schemas']['MessageRating']['rating']>;
