import { TasksScreen } from "@/components/screens/TasksScreen";
import AuthenticatedShell from "../AuthenticatedShell";

export default function Page() { return <AuthenticatedShell><TasksScreen /></AuthenticatedShell>; }
