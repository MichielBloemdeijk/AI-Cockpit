import TasksPage from "../../../app/(app)/tasks/page";
import AuthenticatedShell from "../AuthenticatedShell";

export default function Page() { return <AuthenticatedShell><TasksPage /></AuthenticatedShell>; }
