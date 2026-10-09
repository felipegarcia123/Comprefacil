import { notFound } from "next/navigation";
import { UiKit } from "./UiKit";

export const metadata = { title: "Mostrador · UI kit" };

/** Development-only gallery of the design-system primitives. Not served in production builds. */
export default function UiKitPage() {
  if (process.env.NODE_ENV === "production" && process.env.NEXT_PUBLIC_UI_KIT !== "1") notFound();
  return <UiKit />;
}
