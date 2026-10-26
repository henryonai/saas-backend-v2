/**
 * The HTTP API's request and response shapes, from the generated OpenAPI types
 * (`pnpm gen:api`). The documents describe inputs, so fields with a default
 * (ids, timestamps) come out optional; Pydantic always sends them in a
 * response, hence `Sent<...>`.
 */
import type { Message } from '@ag-ui/client';
import type { components as Accounts } from './generated/accounts';
import type { components as Assistant } from './generated/assistant';
import type { components as Billing } from './generated/billing';
import type { components as Notifications } from './generated/notifications';
import type { components as Orders } from './generated/orders';

type Sent<T> = Required<T>;

export type Tenant = Sent<Accounts['schemas']['TenantOut']>;
export type TenantCreate = Accounts['schemas']['TenantCreate'];
export type Member = Sent<Accounts['schemas']['Member']>;
export type Invitation = Sent<Accounts['schemas']['Invitation']>;
export type InviteRequest = Accounts['schemas']['InviteRequest'];
export type RoleUpdate = Accounts['schemas']['RoleUpdate'];

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
/** A sent message's run: AgentCore streams its answer (the AG-UI runId). */
export type AssistantRun = Sent<Assistant['schemas']['RunOut']>;
/** The user's thumbs up or down on an answer. */
export type AssistantRating = NonNullable<Assistant['schemas']['MessageRating']['rating']>;
