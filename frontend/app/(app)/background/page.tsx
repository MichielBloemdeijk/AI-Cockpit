import { permanentRedirect } from "next/navigation";

export default function BackgroundRunsPage() {
  permanentRedirect("/tasks");
}
