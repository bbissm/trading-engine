CREATE TABLE "account" (
	"id" text PRIMARY KEY NOT NULL,
	"mode" text NOT NULL,
	"name" text NOT NULL,
	"currency" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "autopilot" (
	"account_id" text PRIMARY KEY NOT NULL,
	"state" text NOT NULL,
	"reason" text,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "episode" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"account_id" text NOT NULL,
	"number" integer NOT NULL,
	"reason" text NOT NULL,
	"start_cash" numeric(28, 10) NOT NULL,
	"cash" numeric(28, 10) NOT NULL,
	"fees_paid" numeric(28, 10) DEFAULT '0' NOT NULL,
	"realized" numeric(28, 10) DEFAULT '0' NOT NULL,
	"policy" jsonb NOT NULL,
	"strategy_version_ids" jsonb NOT NULL,
	"cost_model" text NOT NULL,
	"sim_version" text NOT NULL,
	"sim_through" timestamp with time zone NOT NULL,
	"signals_through" timestamp with time zone NOT NULL,
	"started_at" timestamp with time zone DEFAULT now() NOT NULL,
	"ended_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "equity_snapshot" (
	"account_id" text NOT NULL,
	"episode_id" integer NOT NULL,
	"ts" timestamp with time zone NOT NULL,
	"equity" numeric(28, 10) NOT NULL,
	"cash" numeric(28, 10) NOT NULL,
	"invested" numeric(28, 10) NOT NULL,
	CONSTRAINT "equity_snapshot_account_id_episode_id_ts_pk" PRIMARY KEY("account_id","episode_id","ts")
);
--> statement-breakpoint
CREATE TABLE "fill" (
	"id" text PRIMARY KEY NOT NULL,
	"order_id" text NOT NULL,
	"qty" numeric(28, 10) NOT NULL,
	"price" numeric(28, 10) NOT NULL,
	"fee" numeric(28, 10) NOT NULL,
	"fee_currency" text NOT NULL,
	"time" timestamp with time zone NOT NULL,
	"simulated" boolean NOT NULL
);
--> statement-breakpoint
CREATE TABLE "trade_order" (
	"id" text PRIMARY KEY NOT NULL,
	"intent_key" text NOT NULL,
	"mode" text NOT NULL,
	"account_id" text NOT NULL,
	"episode_id" integer NOT NULL,
	"instrument_id" text NOT NULL,
	"strategy_version_id" text NOT NULL,
	"signal_id" uuid,
	"trade_id" text,
	"timeframe" text NOT NULL,
	"side" text NOT NULL,
	"type" text NOT NULL,
	"role" text NOT NULL,
	"qty" numeric(28, 10) NOT NULL,
	"limit_price" numeric(28, 10),
	"stop_price" numeric(28, 10),
	"state" text NOT NULL,
	"exit_plan" jsonb,
	"reason" text,
	"created_at" timestamp with time zone NOT NULL,
	"valid_until" timestamp with time zone,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "reservation" (
	"intent_key" text PRIMARY KEY NOT NULL,
	"account_id" text NOT NULL,
	"episode_id" integer NOT NULL,
	"instrument_id" text NOT NULL,
	"qty" numeric(28, 10) NOT NULL,
	"cash" numeric(28, 10) NOT NULL,
	"risk" numeric(28, 10) NOT NULL
);
--> statement-breakpoint
CREATE TABLE "signal_outcome" (
	"signal_id" uuid NOT NULL,
	"account_id" text NOT NULL,
	"episode_id" integer NOT NULL,
	"status" text NOT NULL,
	"reasons" jsonb NOT NULL,
	"values" jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "signal_outcome_signal_id_account_id_episode_id_pk" PRIMARY KEY("signal_id","account_id","episode_id")
);
--> statement-breakpoint
CREATE TABLE "trade" (
	"id" text PRIMARY KEY NOT NULL,
	"account_id" text NOT NULL,
	"episode_id" integer NOT NULL,
	"instrument_id" text NOT NULL,
	"strategy_version_id" text NOT NULL,
	"signal_id" uuid,
	"timeframe" text NOT NULL,
	"status" text NOT NULL,
	"opened_at" timestamp with time zone NOT NULL,
	"closed_at" timestamp with time zone,
	"qty" numeric(28, 10) NOT NULL,
	"entry_value" numeric(28, 10) NOT NULL,
	"entry_fees" numeric(28, 10) NOT NULL,
	"exit_value" numeric(28, 10),
	"exit_fees" numeric(28, 10),
	"net" numeric(28, 10),
	"planned_stop" numeric(28, 10) NOT NULL,
	"planned_risk" numeric(28, 10) NOT NULL,
	"current_stop" numeric(28, 10) NOT NULL,
	"highest_close" numeric(28, 10) NOT NULL,
	"bars_held" integer DEFAULT 0 NOT NULL,
	"exit_plan" jsonb NOT NULL,
	"exit_reason" text,
	"managed_through" timestamp with time zone NOT NULL
);
--> statement-breakpoint
ALTER TABLE "signal" ADD COLUMN "ref_level" numeric(28, 10);--> statement-breakpoint
ALTER TABLE "autopilot" ADD CONSTRAINT "autopilot_account_id_account_id_fk" FOREIGN KEY ("account_id") REFERENCES "public"."account"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "episode" ADD CONSTRAINT "episode_account_id_account_id_fk" FOREIGN KEY ("account_id") REFERENCES "public"."account"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "equity_snapshot" ADD CONSTRAINT "equity_snapshot_account_id_account_id_fk" FOREIGN KEY ("account_id") REFERENCES "public"."account"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "fill" ADD CONSTRAINT "fill_order_id_trade_order_id_fk" FOREIGN KEY ("order_id") REFERENCES "public"."trade_order"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "trade_order" ADD CONSTRAINT "trade_order_instrument_id_instrument_id_fk" FOREIGN KEY ("instrument_id") REFERENCES "public"."instrument"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "trade_order" ADD CONSTRAINT "trade_order_strategy_version_id_strategy_version_id_fk" FOREIGN KEY ("strategy_version_id") REFERENCES "public"."strategy_version"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "reservation" ADD CONSTRAINT "reservation_account_id_account_id_fk" FOREIGN KEY ("account_id") REFERENCES "public"."account"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "signal_outcome" ADD CONSTRAINT "signal_outcome_signal_id_signal_id_fk" FOREIGN KEY ("signal_id") REFERENCES "public"."signal"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "signal_outcome" ADD CONSTRAINT "signal_outcome_account_id_account_id_fk" FOREIGN KEY ("account_id") REFERENCES "public"."account"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "trade" ADD CONSTRAINT "trade_account_id_account_id_fk" FOREIGN KEY ("account_id") REFERENCES "public"."account"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "trade" ADD CONSTRAINT "trade_instrument_id_instrument_id_fk" FOREIGN KEY ("instrument_id") REFERENCES "public"."instrument"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "trade" ADD CONSTRAINT "trade_strategy_version_id_strategy_version_id_fk" FOREIGN KEY ("strategy_version_id") REFERENCES "public"."strategy_version"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
CREATE UNIQUE INDEX "account_id_mode_idx" ON "account" USING btree ("id","mode");--> statement-breakpoint
CREATE UNIQUE INDEX "episode_account_number_idx" ON "episode" USING btree ("account_id","number");--> statement-breakpoint
CREATE INDEX "fill_order_idx" ON "fill" USING btree ("order_id");--> statement-breakpoint
CREATE UNIQUE INDEX "trade_order_intent_idx" ON "trade_order" USING btree ("intent_key");--> statement-breakpoint
CREATE INDEX "trade_order_account_idx" ON "trade_order" USING btree ("account_id","episode_id","state");--> statement-breakpoint
CREATE INDEX "trade_account_idx" ON "trade" USING btree ("account_id","episode_id","status");--> statement-breakpoint
ALTER TABLE "trade_order" ADD CONSTRAINT "trade_order_account_mode_fk" FOREIGN KEY ("account_id","mode") REFERENCES "account"("id","mode");--> statement-breakpoint
UPDATE "schema_meta" SET "version" = 2, "updated_at" = now() WHERE "id" = 1;
