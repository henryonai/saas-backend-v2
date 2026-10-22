"""Creates fake DynamoDB tables (moto). Call only inside an active mock."""
import boto3


def create_table(name: str, with_gsi1: bool = False):
    """Creates a table with pk/sk keys, matching template.yaml, and returns it."""
    attributes = ["pk", "sk"] + (["gsi1pk", "gsi1sk"] if with_gsi1 else [])
    params = {
        "TableName": name,
        "BillingMode": "PAY_PER_REQUEST",
        "AttributeDefinitions": [{"AttributeName": a, "AttributeType": "S"} for a in attributes],
        "KeySchema": [
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
    }
    if with_gsi1:
        params["GlobalSecondaryIndexes"] = [{
            "IndexName": "gsi1",
            "KeySchema": [
                {"AttributeName": "gsi1pk", "KeyType": "HASH"},
                {"AttributeName": "gsi1sk", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }]
    boto3.client("dynamodb").create_table(**params)
    return boto3.resource("dynamodb").Table(name)
