import { PageTransition } from "@/components/page-transition";

/** Remounted on navigation → page transition (direction by navigation depth). */
export default function Template({ children }: { children: React.ReactNode }) {
  return <PageTransition>{children}</PageTransition>;
}
