CREATE TABLE "audit_event" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"ts" timestamp with time zone DEFAULT now() NOT NULL,
	"actor" text NOT NULL,
	"kind" text NOT NULL,
	"object" text,
	"data" jsonb
);
--> statement-breakpoint
CREATE TABLE "candle" (
	"instrument_id" text NOT NULL,
	"timeframe" text NOT NULL,
	"open_time" timestamp with time zone NOT NULL,
	"close_time" timestamp with time zone NOT NULL,
	"open" numeric(28, 10) NOT NULL,
	"high" numeric(28, 10) NOT NULL,
	"low" numeric(28, 10) NOT NULL,
	"close" numeric(28, 10) NOT NULL,
	"volume" numeric(28, 10) NOT NULL,
	"trades" integer,
	"source" text NOT NULL,
	"available_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "candle_instrument_id_timeframe_open_time_pk" PRIMARY KEY("instrument_id","timeframe","open_time")
);
--> statement-breakpoint
CREATE TABLE "command" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"type" text NOT NULL,
	"target" text,
	"params" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"issued_by" text NOT NULL,
	"issued_at" timestamp with time zone DEFAULT now() NOT NULL,
	"status" text DEFAULT 'PENDING' NOT NULL,
	"result" jsonb,
	"handled_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "feature_snapshot" (
	"instrument_id" text NOT NULL,
	"timeframe" text NOT NULL,
	"candle_close" timestamp with time zone NOT NULL,
	"regime_rule_version" text NOT NULL,
	"regime" text NOT NULL,
	"features" jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "feature_snapshot_instrument_id_timeframe_candle_close_regime_rule_version_pk" PRIMARY KEY("instrument_id","timeframe","candle_close","regime_rule_version")
);
--> statement-breakpoint
CREATE TABLE "feed_status" (
	"feed" text NOT NULL,
	"instrument_id" text NOT NULL,
	"timeframe" text NOT NULL,
	"last_candle_close" timestamp with time zone,
	"last_ok_at" timestamp with time zone,
	"status" text NOT NULL,
	"detail" text,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "feed_status_feed_instrument_id_timeframe_pk" PRIMARY KEY("feed","instrument_id","timeframe")
);
--> statement-breakpoint
CREATE TABLE "heartbeat" (
	"service" text PRIMARY KEY NOT NULL,
	"last_seen" timestamp with time zone NOT NULL,
	"schema_version" integer NOT NULL,
	"detail" jsonb
);
--> statement-breakpoint
CREATE TABLE "instrument" (
	"id" text PRIMARY KEY NOT NULL,
	"kind" text NOT NULL,
	"venue" text NOT NULL,
	"venue_symbol" text NOT NULL,
	"name" text NOT NULL,
	"base_asset" text,
	"quote_currency" text NOT NULL,
	"tick_size" numeric(28, 10),
	"min_qty" numeric(28, 10),
	"min_notional" numeric(28, 10),
	"in_universe" boolean DEFAULT false NOT NULL,
	"leader_id" text,
	"status" text DEFAULT 'ACTIVE' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "schema_meta" (
	"id" integer PRIMARY KEY NOT NULL,
	"version" integer NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "signal" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"instrument_id" text NOT NULL,
	"timeframe" text NOT NULL,
	"candle_close" timestamp with time zone NOT NULL,
	"strategy_version_id" text NOT NULL,
	"action" text NOT NULL,
	"regime" text NOT NULL,
	"score" integer,
	"entry" numeric(28, 10),
	"stop" numeric(28, 10),
	"target" numeric(28, 10),
	"max_hold_bars" integer,
	"valid_until" timestamp with time zone,
	"triggers" jsonb NOT NULL,
	"counter" jsonb NOT NULL,
	"data_source" text NOT NULL,
	"data_age_s" integer NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "strategy_version" (
	"id" text PRIMARY KEY NOT NULL,
	"strategy" text NOT NULL,
	"version" integer NOT NULL,
	"params" jsonb NOT NULL,
	"regime_rule_version" text NOT NULL,
	"code_commit" text,
	"lifecycle_status" text DEFAULT 'IDEE' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "candle" ADD CONSTRAINT "candle_instrument_id_instrument_id_fk" FOREIGN KEY ("instrument_id") REFERENCES "public"."instrument"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "feature_snapshot" ADD CONSTRAINT "feature_snapshot_instrument_id_instrument_id_fk" FOREIGN KEY ("instrument_id") REFERENCES "public"."instrument"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "feed_status" ADD CONSTRAINT "feed_status_instrument_id_instrument_id_fk" FOREIGN KEY ("instrument_id") REFERENCES "public"."instrument"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "signal" ADD CONSTRAINT "signal_instrument_id_instrument_id_fk" FOREIGN KEY ("instrument_id") REFERENCES "public"."instrument"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "signal" ADD CONSTRAINT "signal_strategy_version_id_strategy_version_id_fk" FOREIGN KEY ("strategy_version_id") REFERENCES "public"."strategy_version"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "audit_ts_idx" ON "audit_event" USING btree ("ts");--> statement-breakpoint
CREATE INDEX "command_pending_idx" ON "command" USING btree ("status","id");--> statement-breakpoint
CREATE UNIQUE INDEX "signal_unique_idx" ON "signal" USING btree ("strategy_version_id","instrument_id","timeframe","candle_close");--> statement-breakpoint
CREATE INDEX "signal_instrument_idx" ON "signal" USING btree ("instrument_id","candle_close");--> statement-breakpoint
INSERT INTO "schema_meta" ("id", "version") VALUES (1, 1);
