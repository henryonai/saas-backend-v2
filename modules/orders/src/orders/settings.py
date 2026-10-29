from shared.settings import BaseAppSettings


class OrdersSettings(BaseAppSettings):
    orders_table: str
    exports_bucket: str | None = None   # the listener writes orders' part of a workspace's export there
