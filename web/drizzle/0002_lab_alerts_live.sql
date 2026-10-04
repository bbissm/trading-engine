CREATE TABLE "alert_delivery" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"alert_id" integer NOT NULL,
	"channel" text NOT NULL,
	"at" timestamp with time zone DEFAULT now() NOT NULL,
	"status" text NOT NULL,
	"provider_ref" text,
	"error" text
);
--> statement-breakpoint
CREATE TABLE "alert" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	"level" text NOT NULL,
	"mode" text NOT NULL,
	"kind" text NOT NULL,
	"dedup_key" text NOT NULL,
	"title" text NOT NULL,
	"body" text NOT NULL,
	"data" jsonb,
	"status" text DEFAULT 'OPEN' NOT NULL,
	"occurrences" integer DEFAULT 1 NOT NULL,
	"acknowledged_at" timestamp with time zone,
	"acknowledged_by" text,
	"resolved_at" timestamp with time zone,
	"escalation_level" integer DEFAULT 0 NOT NULL,
	"next_escalation_at" timestamp with time zone,
	"last_sent_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "approval" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"strategy_version_id" text NOT NULL,
	"proposed_at" timestamp with time zone DEFAULT now() NOT NULL,
	"proposed_by" text NOT NULL,
	"decision" text DEFAULT 'PENDING' NOT NULL,
	"decided_at" timestamp with time zone,
	"decided_by" text,
	"note" text,
	"replaces_version_id" text
);
--> statement-breakpoint
CREATE TABLE "channel_status" (
	"channel" text PRIMARY KEY NOT NULL,
	"configured" boolean DEFAULT false NOT NULL,
	"ok" boolean DEFAULT false NOT NULL,
	"detail" text,
	"last_check_at" timestamp with time zone,
	"last_test_sent_at" timestamp with time zone,
	"last_test_ack_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "execution_metric" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"account_id" text NOT NULL,
	"order_id" text NOT NULL,
	"signal_to_order_ms" integer,
	"order_to_ack_ms" integer,
	"fill_to_protect_ms" integer,
	"slippage_bps" numeric(14, 4),
	"at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "experiment_trial" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"experiment_id" integer NOT NULL,
	"variant" jsonb NOT NULL,
	"metrics" jsonb NOT NULL,
	"folds" jsonb,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "experiment" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"kind" text NOT NULL,
	"strategy" text NOT NULL,
	"strategy_version_id" text,
	"hypothesis" text NOT NULL,
	"config" jsonb NOT NULL,
	"dataset_hash" text,
	"data_from" timestamp with time zone,
	"data_to" timestamp with time zone,
	"status" text NOT NULL,
	"outcome" text,
	"summary" jsonb,
	"progress" jsonb,
	"variants_tested" integer DEFAULT 0 NOT NULL,
	"cpu_seconds" numeric(14, 3) DEFAULT '0' NOT NULL,
	"started_at" timestamp with time zone,
	"finished_at" timestamp with time zone,
	"issued_by" text NOT NULL
);
--> statement-breakpoint
CREATE TABLE "fx_rate" (
	"base" text NOT NULL,
	"quote" text NOT NULL,
	"date" text NOT NULL,
	"rate" numeric(28, 10) NOT NULL,
	"source" text NOT NULL,
	"fetched_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "fx_rate_base_quote_date_pk" PRIMARY KEY("base","quote","date")
);
--> statement-breakpoint
CREATE TABLE "gate_evaluation" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"strategy_version_id" text NOT NULL,
	"gate" text NOT NULL,
	"result" text NOT NULL,
	"criteria" jsonb NOT NULL,
	"reasons" jsonb NOT NULL,
	"gate_config_version" text NOT NULL,
	"experiment_id" integer,
	"evaluated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "holdout_access" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"strategy" text NOT NULL,
	"experiment_id" integer NOT NULL,
	"accessed_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "mandate" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"account_id" text NOT NULL,
	"autonomy_level" integer NOT NULL,
	"strategy_version_ids" jsonb NOT NULL,
	"instrument_ids" jsonb NOT NULL,
	"budget" numeric(28, 10) NOT NULL,
	"policy" jsonb NOT NULL,
	"status" text DEFAULT 'DRAFT' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"activated_at" timestamp with time zone,
	"activated_by" text,
	"step_up_at" timestamp with time zone,
	"valid_until" timestamp with time zone,
	"ended_at" timestamp with time zone,
	"end_reason" text
);
--> statement-breakpoint
CREATE TABLE "order_approval" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"account_id" text NOT NULL,
	"signal_id" uuid NOT NULL,
	"intent" jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"expires_at" timestamp with time zone NOT NULL,
	"status" text DEFAULT 'PENDING' NOT NULL,
	"decided_at" timestamp with time zone,
	"decided_by" text
);
--> statement-breakpoint
CREATE TABLE "policy_change" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"account_id" text NOT NULL,
	"policy" jsonb NOT NULL,
	"requested_at" timestamp with time zone DEFAULT now() NOT NULL,
	"requested_by" text NOT NULL,
	"effective_at" timestamp with time zone NOT NULL,
	"status" text DEFAULT 'PENDING' NOT NULL,
	"applied_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "reconciliation" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"account_id" text NOT NULL,
	"at" timestamp with time zone DEFAULT now() NOT NULL,
	"status" text NOT NULL,
	"diffs" jsonb NOT NULL
);
--> statement-breakpoint
CREATE TABLE "setting" (
	"key" text PRIMARY KEY NOT NULL,
	"value" jsonb NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_by" text NOT NULL
);
--> statement-breakpoint
ALTER TABLE "account" ADD COLUMN "provider" text;--> statement-breakpoint
ALTER TABLE "account" ADD COLUMN "provider_account_ref" text;--> statement-breakpoint
ALTER TABLE "account" ADD COLUMN "permissions" jsonb;--> statement-breakpoint
CREATE INDEX "alert_delivery_alert_idx" ON "alert_delivery" USING btree ("alert_id","at");--> statement-breakpoint
CREATE UNIQUE INDEX "alert_dedup_open_idx" ON "alert" USING btree ("dedup_key") WHERE "alert"."status" <> 'RESOLVED';--> statement-breakpoint
CREATE INDEX "alert_status_idx" ON "alert" USING btree ("status","level","created_at");--> statement-breakpoint
CREATE INDEX "experiment_trial_idx" ON "experiment_trial" USING btree ("experiment_id");--> statement-breakpoint
CREATE INDEX "experiment_status_idx" ON "experiment" USING btree ("status","created_at");--> statement-breakpoint
CREATE INDEX "gate_eval_version_idx" ON "gate_evaluation" USING btree ("strategy_version_id","gate","evaluated_at");--> statement-breakpoint
CREATE INDEX "reconciliation_account_idx" ON "reconciliation" USING btree ("account_id","at");--> statement-breakpoint
UPDATE "schema_meta" SET "version" = 3, "updated_at" = now() WHERE "id" = 1;
