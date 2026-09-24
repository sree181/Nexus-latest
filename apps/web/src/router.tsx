import {
  createRootRoute,
  createRoute,
  createRouter,
  Navigate,
  Outlet,
} from "@tanstack/react-router";
import { DeveloperProjectProvider } from "./components/DeveloperProject";
import { DeveloperRail } from "./components/DeveloperRail";
import { ModeBanner } from "./components/ModeBanner";
import { RequiresAnalyst } from "./components/RequiresAnalyst";
import { RequiresCapability } from "./components/RequiresCapability";
import { RouteError, RouteNotFound } from "./components/RouteBoundary";
import { Sidebar } from "./components/Sidebar";
import { RoleLanding } from "./routes/RoleLanding";
import { AnalystQueue } from "./routes/AnalystQueue";
import { AnalystCaseDetail } from "./routes/AnalystCaseDetail";
import { AnalystInvestigation } from "./routes/AnalystInvestigation";
import { CisoOverview } from "./routes/CisoOverview";
import { CisoPolicies } from "./routes/CisoPolicies";
import { CisoApprovals } from "./routes/CisoApprovals";
import { CisoRemediation } from "./routes/CisoRemediation";
import { CisoReports } from "./routes/CisoReports";
import { FleetGraph } from "./routes/FleetGraph";
import { FleetOverview } from "./routes/FleetOverview";
import { FleetRecommendations } from "./routes/FleetRecommendations";
import { RunCode } from "./routes/RunCode";
import { RunLayout } from "./routes/RunLayout";
import { RunMemory } from "./routes/RunMemory";
import { RunRewind } from "./routes/RunRewind";
import { RunSecurity } from "./routes/RunSecurity";
import { RunSupply } from "./routes/RunSupply";
import { RunWorking } from "./routes/RunWorking";
import { Structure } from "./routes/Structure";
import { Audit } from "./routes/Audit";
import { Devices } from "./routes/Devices";
import { StartTask } from "./routes/StartTask";
import { DeveloperSessions } from "./routes/DeveloperSessions";
import { DeveloperAttention } from "./routes/DeveloperAttention";
import { DeveloperReviewDetail } from "./routes/DeveloperReviewDetail";
import { DeveloperSessionLayout } from "./routes/DeveloperSessionLayout";
import { DeveloperSessionOverview } from "./routes/DeveloperSessionOverview";
import { DeveloperSessionActivity } from "./routes/DeveloperSessionActivity";
import { DeveloperSessionSecurity } from "./routes/DeveloperSessionSecurity";
import { DeveloperSessionEvidence } from "./routes/DeveloperSessionEvidence";
import { DeveloperConnectionsLayout } from "./routes/DeveloperConnectionsLayout";
import { DeveloperConnectionsOverview } from "./routes/DeveloperConnectionsOverview";
import { DeveloperConnectionsEditors } from "./routes/DeveloperConnectionsEditors";
import { DeveloperConnectionsRepositories } from "./routes/DeveloperConnectionsRepositories";
import { DeveloperConnectionsDevices } from "./routes/DeveloperConnectionsDevices";
import { AnalystReviews } from "./routes/AnalystReviews";
import { AnalystReviewDetail } from "./routes/AnalystReviewDetail";
import { useIdentity } from "./lib/useIdentity";

/** The banner sits above the rail rather than inside a screen: what it reports
 *  is true of the whole deployment, and a developer who never opens the screen
 *  that happened to carry it would never be told. Everything below it measures
 *  against the row rather than the viewport, so the banner appearing shortens
 *  the app instead of pushing its footer off the bottom. */
function Layout() {
  const { developer } = useIdentity();
  if (developer) {
    return (
      <div className="flex h-screen flex-col bg-paper">
        <ModeBanner />
        <DeveloperProjectProvider>
          <div className="dev-app min-h-0 flex-1">
            <DeveloperRail />
            <Outlet />
          </div>
        </DeveloperProjectProvider>
      </div>
    );
  }
  return (
    <div className="flex h-screen flex-col bg-paper">
      <ModeBanner />
      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <Sidebar />
        <Outlet />
      </div>
    </div>
  );
}

const rootRoute = createRootRoute({
  component: Layout,
  notFoundComponent: RouteNotFound,
  errorComponent: RouteError,
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: RoleLanding,
});

const developerSessionsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/developer/sessions",
  component: () => (
    <RequiresCapability capability="run.own">
      <DeveloperSessions />
    </RequiresCapability>
  ),
});

const developerStartRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/developer/start",
  component: () => (
    <RequiresCapability capability="run.create">
      <StartTask />
    </RequiresCapability>
  ),
});

const developerAttentionRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/developer/attention",
  component: () => (
    <RequiresCapability capability="review.own">
      <DeveloperAttention />
    </RequiresCapability>
  ),
});

const developerReviewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/developer/reviews/$requestId",
  component: () => (
    <RequiresCapability capability="review.own">
      <DeveloperReviewDetail />
    </RequiresCapability>
  ),
});

const developerSessionRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/developer/sessions/$sessionId",
  component: () => (
    <RequiresCapability capability="run.own">
      <DeveloperSessionLayout />
    </RequiresCapability>
  ),
});

const developerSessionActivityRoute = createRoute({
  getParentRoute: () => developerSessionRoute,
  path: "activity",
  component: DeveloperSessionActivity,
});

const developerSessionOverviewRoute = createRoute({
  getParentRoute: () => developerSessionRoute,
  path: "/",
  component: DeveloperSessionOverview,
});

const developerSessionSecurityRoute = createRoute({
  getParentRoute: () => developerSessionRoute,
  path: "security",
  component: DeveloperSessionSecurity,
});

const developerSessionEvidenceRoute = createRoute({
  getParentRoute: () => developerSessionRoute,
  path: "evidence",
  component: DeveloperSessionEvidence,
});

const developerConnectionsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/developer/connections",
  component: () => (
    <RequiresCapability capability="run.own">
      <DeveloperConnectionsLayout />
    </RequiresCapability>
  ),
});

const developerConnectionsOverviewRoute = createRoute({
  getParentRoute: () => developerConnectionsRoute,
  path: "/",
  component: DeveloperConnectionsOverview,
});

const developerConnectionsEditorsRoute = createRoute({
  getParentRoute: () => developerConnectionsRoute,
  path: "editors",
  component: DeveloperConnectionsEditors,
});

const developerConnectionsRepositoriesRoute = createRoute({
  getParentRoute: () => developerConnectionsRoute,
  path: "repositories",
  component: DeveloperConnectionsRepositories,
});

const developerConnectionsDevicesRoute = createRoute({
  getParentRoute: () => developerConnectionsRoute,
  path: "devices",
  component: DeveloperConnectionsDevices,
});

const analystQueueRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/analyst/queue",
  component: () => <RequiresCapability capability="case.read"><AnalystQueue /></RequiresCapability>,
});

const analystReviewsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/analyst/reviews",
  component: () => <RequiresCapability capability="review.read"><AnalystReviews /></RequiresCapability>,
});

const analystReviewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/analyst/reviews/$requestId",
  component: () => <RequiresCapability capability="review.read"><AnalystReviewDetail /></RequiresCapability>,
});

const analystCaseRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/analyst/cases/$caseId",
  component: () => <RequiresCapability capability="case.read"><AnalystCaseDetail /></RequiresCapability>,
});

const analystInvestigationRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/analyst/investigate/$runId/$findingId",
  component: () => <RequiresCapability capability="evidence.read"><AnalystInvestigation /></RequiresCapability>,
});

const cisoOverviewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/ciso/overview",
  component: () => <RequiresCapability capability="policy.write"><CisoOverview /></RequiresCapability>,
});

const cisoPoliciesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/ciso/policies",
  component: () => <RequiresCapability capability="policy.write"><CisoPolicies /></RequiresCapability>,
});

const cisoApprovalsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/ciso/approvals",
  component: () => <RequiresCapability capability="exception.approve"><CisoApprovals /></RequiresCapability>,
});

const cisoRemediationRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/ciso/remediation",
  component: () => <RequiresCapability capability="remediation.write"><CisoRemediation /></RequiresCapability>,
});

const cisoReportsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/ciso/reports",
  component: () => <RequiresCapability capability="report.generate"><CisoReports /></RequiresCapability>,
});

const auditRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/audit",
  component: () => (
    <RequiresAnalyst>
      <Audit />
    </RequiresAnalyst>
  ),
});

/** Not wrapped in RequiresAnalyst: a developer registers their own laptop,
 *  and approving a login is the one governance action that is theirs. */
const devicesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/devices",
  component: Devices,
});

/** The developer's own screen, and the reason they no longer need a terminal
 *  walkthrough to get their editor reporting. */
const setupRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/setup",
  component: () => <Navigate to="/developer/connections/editors" replace />,
});

const structureRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/structure",
  component: () => (
    <RequiresAnalyst>
      <Structure />
    </RequiresAnalyst>
  ),
});

const fleetRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/fleet",
  component: () => (
    <RequiresAnalyst>
      <FleetGraph />
    </RequiresAnalyst>
  ),
});

const fleetOverviewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/fleet/overview",
  component: () => (
    <RequiresAnalyst>
      <FleetOverview />
    </RequiresAnalyst>
  ),
});

const fleetRecommendationsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/fleet/recommendations",
  component: () => (
    <RequiresAnalyst>
      <FleetRecommendations />
    </RequiresAnalyst>
  ),
});

/** The four views over one run share their chrome, so they nest under a layout
 *  that owns the breadcrumb and the tab bar. */
const runRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/runs/$runId",
  component: RunLayout,
});

const runWorkingRoute = createRoute({
  getParentRoute: () => runRoute,
  path: "/",
  component: RunWorking,
});

const runCodeRoute = createRoute({
  getParentRoute: () => runRoute,
  path: "code",
  component: RunCode,
});

const runMemoryRoute = createRoute({
  getParentRoute: () => runRoute,
  path: "memory",
  component: RunMemory,
});

/** The instant being asked about lives in the URL, like every other view here:
 *  "what did it know at 14:03" is a thing you send someone, and an answer that
 *  only exists in one browser's memory is not evidence. */
const runRewindRoute = createRoute({
  getParentRoute: () => runRoute,
  path: "rewind",
  component: RunRewind,
  validateSearch: (search: Record<string, unknown>): { at?: number } => {
    const at = Number(search.at);
    return Number.isInteger(at) && at > 0 ? { at } : {};
  },
});

const runSecurityRoute = createRoute({
  getParentRoute: () => runRoute,
  path: "security",
  component: RunSecurity,
});

const runSupplyRoute = createRoute({
  getParentRoute: () => runRoute,
  path: "supply",
  component: RunSupply,
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  developerSessionsRoute,
  developerStartRoute,
  developerAttentionRoute,
  developerReviewRoute,
  developerSessionRoute.addChildren([
    developerSessionOverviewRoute,
    developerSessionActivityRoute,
    developerSessionSecurityRoute,
    developerSessionEvidenceRoute,
  ]),
  developerConnectionsRoute.addChildren([
    developerConnectionsOverviewRoute,
    developerConnectionsEditorsRoute,
    developerConnectionsRepositoriesRoute,
    developerConnectionsDevicesRoute,
  ]),
  analystQueueRoute,
  analystReviewsRoute,
  analystReviewRoute,
  analystCaseRoute,
  analystInvestigationRoute,
  cisoOverviewRoute,
  cisoPoliciesRoute,
  cisoApprovalsRoute,
  cisoRemediationRoute,
  cisoReportsRoute,
  structureRoute,
  auditRoute,
  devicesRoute,
  setupRoute,
  fleetRoute,
  fleetOverviewRoute,
  fleetRecommendationsRoute,
  runRoute.addChildren([
    runWorkingRoute,
    runCodeRoute,
    runMemoryRoute,
    runRewindRoute,
    runSecurityRoute,
    runSupplyRoute,
  ]),
]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
