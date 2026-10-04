import { redirect } from "next/navigation";

/** The experiment list lives on «Strategien & Lernlabor»; /lab only exists for its detail pages. */
export default function LabIndex() {
  redirect("/strategies");
}
