# Terraform Changes Needed for Celery SQS Migration

## Context
The Django app is migrating from database-backed Celery broker to AWS SQS for better production reliability. The Django code changes are complete, but terraform needs to be updated.

## Required Changes

### 1. IAM Role Permissions for SQS

Add SQS permissions to the EC2 instance IAM role (both main app instances and celery-beat instance):

```hcl
# Add to the IAM role policy
{
  "Effect": "Allow",
  "Action": [
    "sqs:CreateQueue",
    "sqs:DeleteQueue",
    "sqs:GetQueueUrl",
    "sqs:GetQueueAttributes",
    "sqs:SetQueueAttributes",
    "sqs:SendMessage",
    "sqs:ReceiveMessage",
    "sqs:DeleteMessage",
    "sqs:ChangeMessageVisibility",
    "sqs:PurgeQueue",
    "sqs:ListQueues"
  ],
  "Resource": "arn:aws:sqs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:superschedules-*"
}
```

### 2. Environment Variables

Update both `user_data.sh.tftpl` and `celery_beat_user_data.sh.tftpl` to include:

```bash
# In the docker-compose.yml environment section for both django and celery services:
- AWS_REGION=${region}
- USE_SQS_BROKER=True
```

### 3. Security Groups (Optional but Recommended)

Since SQS is AWS-managed, you can remove the security group rules that allowed EC2 → RDS communication ONLY for Celery (keep them for Django database access). The recent security group fix you made for celery-beat → RDS can be simplified.

### 4. SQS Queue Creation (Optional)

You can either:
- **Option A (Recommended)**: Let Celery auto-create queues on first use (simpler, less terraform code)
- **Option B**: Pre-create SQS queues in terraform for better control:

```hcl
resource "aws_sqs_queue" "celery_default" {
  name                       = "superschedules-default"
  visibility_timeout_seconds = 3600
  message_retention_seconds  = 1209600  # 14 days

  tags = {
    Environment = var.environment
    Application = "superschedules"
  }
}

resource "aws_sqs_queue" "celery_embeddings" {
  name                       = "superschedules-embeddings"
  visibility_timeout_seconds = 3600
  message_retention_seconds  = 1209600
}

resource "aws_sqs_queue" "celery_geocoding" {
  name                       = "superschedules-geocoding"
  visibility_timeout_seconds = 3600
  message_retention_seconds  = 1209600
}

resource "aws_sqs_queue" "celery_scraping" {
  name                       = "superschedules-scraping"
  visibility_timeout_seconds = 3600
  message_retention_seconds  = 1209600
}
```

## Testing

After terraform apply:

1. Check EC2 instance has SQS permissions:
   ```bash
   aws sts get-caller-identity
   aws sqs list-queues --queue-name-prefix superschedules-
   ```

2. Check Celery worker logs in CloudWatch:
   ```bash
   aws logs tail /aws/superschedules/prod/app --follow --filter-pattern celery-worker
   ```

3. In Django admin, run a periodic task and verify it executes

## Rollback Plan

If SQS doesn't work, you can rollback by:
1. Set `USE_SQS_BROKER=False` in environment variables
2. Restore the RDS security group rules for celery-beat
3. Restart containers

## Current Files to Modify

- `terraform/prod/iam.tf` (or wherever EC2 IAM role is defined)
- `terraform/prod/templates/user_data.sh.tftpl`
- `terraform/prod/templates/celery_beat_user_data.sh.tftpl`
- (Optional) `terraform/prod/sqs.tf` (if pre-creating queues)
