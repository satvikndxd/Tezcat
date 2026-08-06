# AWS Deployment

Prereqs: AWS account, AWS CLI configured, SAM CLI, Docker (for `sam build`).

```bash
pip install aws-sam-cli
pip install mangum boto3          # extra deps for Lambda handlers
cd infra/templates
sam build
sam deploy --guided --stack-name tezcat
```

The stack provisions:

- **API Gateway (HTTP API)** → `tezcat-control` Lambda serving the FastAPI app (`/api/*`)
- **`tezcat-worker` Lambda** — EventBridge-triggered, runs simulation chunks (`TEZCAT_CHUNK_STEPS`, default 5000), checkpoints to DynamoDB, self-reschedules until done
- **DynamoDB**: `TezcatExperiments`, `TezcatRuns` (pay-per-request)
- **S3**: `tezcat-lab-<account>` — private (all public access blocked), 90-day lifecycle expiry, layout:
  `experiments/{experiment_id}/runs/{run_id}/{trades,snapshots,metrics,shocks,regimes,report}/`
- **CloudWatch**: Lambda logs + `tezcat-worker-errors` alarm

## Environment variables

| Var | Meaning |
|---|---|
| `TEZCAT_STORE` | `aws` in Lambda, `local` for dev |
| `TEZCAT_S3_BUCKET` | artifact bucket |
| `TEZCAT_TABLE_EXPERIMENTS` / `TEZCAT_TABLE_RUNS` | DynamoDB tables |
| `TEZCAT_CHUNK_STEPS` | steps per worker invocation |

## Post-deploy checklist

- [ ] Create an AWS Budget alarm (free-tier guard)
- [ ] Verify the bucket blocks public access (`aws s3api get-public-access-block`)
- [ ] `POST /api/presets/flash_crash/experiments` then create a run; confirm the
      worker chain completes and `report/report.json` lands in S3
- [ ] Check CloudWatch logs for `tezcat-control` and `tezcat-worker`
- [ ] Signed download URLs: `AwsStore.presigned_url(prefix, "report/report.json")`

## Cost controls

Serverless + pay-per-request DynamoDB + private S3 with lifecycle expiry keeps an
idle deployment at ~$0. Step-level data goes to S3, never DynamoDB. Limit log
retention on the two log groups if you run many experiments.
