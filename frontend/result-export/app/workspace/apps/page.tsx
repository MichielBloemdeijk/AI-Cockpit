import { AppsScreen } from "@/components/screens/AppsScreen";
import AuthenticatedShell from "../../AuthenticatedShell";

export default function Page() { return <AuthenticatedShell><AppsScreen appsAvailable={false} /></AuthenticatedShell>; }
