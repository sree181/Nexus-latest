import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
} from "@tanstack/react-router";
import { ModeBanner } from "./components/ModeBanner";
import { RequiresAnalyst } from "./components/RequiresAnalyst";
import { RouteError, RouteNotFound } from "./components/RouteBoundary";
import { Sidebar } from "./components/Sidebar";
import { StartTask } from "./routes/StartTask";
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
import { Setup } from "./routes/Setup";

/** The banner sits above the rail rather than inside a screen: what it reports
 *  is true of the whole deployment, and a developer who never opens the screen
 *  that happened to carry it would never be told. Everything below it measures
 *  against the row rather than the viewport, so the banner appearing shortens
 *  the app instead of pushing its footer off the bottom. */
function Layout() {
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
  component: StartTask,
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
  component: Setup,
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
