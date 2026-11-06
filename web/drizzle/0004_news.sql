CREATE TABLE "llm_bridge_request" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"key" text NOT NULL,
	"purpose" text NOT NULL,
	"status" text DEFAULT 'pending' NOT NULL,
	"request" jsonb NOT NULL,
	"response" jsonb,
	"error" text,
	"error_code" text,
	"claimed_at" timestamp with time zone,
	"processed_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "market_briefing" (
	"day" text PRIMARY KEY NOT NULL,
	"headline" text NOT NULL,
	"summary" text NOT NULL,
	"coins" jsonb NOT NULL,
	"watch" jsonb NOT NULL,
	"item_count" integer NOT NULL,
	"model" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "news_assessment" (
	"item_id" text PRIMARY KEY NOT NULL,
	"coins" jsonb NOT NULL,
	"scope" text NOT NULL,
	"category" text NOT NULL,
	"direction" integer NOT NULL,
	"credibility" text NOT NULL,
	"horizon" text NOT NULL,
	"relevance" integer NOT NULL,
	"rationale" text NOT NULL,
	"model" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "news_item" (
	"id" text PRIMARY KEY NOT NULL,
	"source" text NOT NULL,
	"url" text NOT NULL,
	"title" text NOT NULL,
	"summary" text,
	"published_at" timestamp with time zone,
	"fetched_at" timestamp with time zone DEFAULT now() NOT NULL,
	"assessed_at" timestamp with time zone
);
--> statement-breakpoint
ALTER TABLE "news_assessment" ADD CONSTRAINT "news_assessment_item_id_news_item_id_fk" FOREIGN KEY ("item_id") REFERENCES "public"."news_item"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
CREATE UNIQUE INDEX "llm_bridge_request_key_uq" ON "llm_bridge_request" USING btree ("key");--> statement-breakpoint
CREATE INDEX "llm_bridge_request_status_idx" ON "llm_bridge_request" USING btree ("status","created_at");--> statement-breakpoint
CREATE INDEX "news_assessment_created_idx" ON "news_assessment" USING btree ("created_at");--> statement-breakpoint
CREATE INDEX "news_item_fetched_idx" ON "news_item" USING btree ("fetched_at");--> statement-breakpoint
CREATE INDEX "news_item_unassessed_idx" ON "news_item" USING btree ("assessed_at","fetched_at");--> statement-breakpoint
UPDATE "schema_meta" SET "version" = 5, "updated_at" = now() WHERE "id" = 1;