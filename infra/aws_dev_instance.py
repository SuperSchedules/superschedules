"""Utilities for creating a development EC2 instance."""

import os

import boto3


def create_dev_instance():
    """Create an EC2 instance for development using environment configuration."""
    ec2 = boto3.resource("ec2", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    instances = ec2.create_instances(
        ImageId=os.environ["DEV_AMI_ID"],
        MinCount=1,
        MaxCount=1,
        InstanceType="t3.micro",
        KeyName=os.environ["DEV_KEY_NAME"],
        SecurityGroupIds=[os.environ["DEV_SECURITY_GROUP"]],
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [{"Key": "Name", "Value": "superschedules-dev"}],
            }
        ],
    )
    print("Created instance:", instances[0].id)


if __name__ == "__main__":
    create_dev_instance()
