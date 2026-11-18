import {
  PublicClientApplication,
  InteractionRequiredAuthError,
} from "@azure/msal-browser";

import {
  service,
  factories,
  models,
  type Report,
  type IEmbedConfiguration,
} from "powerbi-client";

import "powerbi-report-authoring";

import type {
  FrontendRenderer,
  FrontendRendererArgs,
} from "@streamlit/component-v2-lib";

type AgentFieldTarget =
  | models.IColumnTarget
  | models.IMeasureTarget
  | models.IColumnAggrTarget;

// A filter described by the app; converted to a Power BI filter below.
type AgentFilter =
  | { kind: "basic"; target: AgentFieldTarget; operator: "In" | "NotIn"; values: Array<string | number | boolean> }
  | { kind: "advanced"; target: AgentFieldTarget; operator: string; value: string | number | boolean }
  | { kind: "topN"; target: AgentFieldTarget; operator: "Top" | "Bottom"; itemCount: number; orderBy: AgentFieldTarget };

type AgentVisualRequest = {
  visualType: string;

  // Existing single-measure requests remain supported.
  category?: models.IColumnTarget;
  value?: models.IMeasureTarget;

  // Advanced visuals can assign multiple fields to different roles.
  fields?: Array<{
    role: string;
    target: AgentFieldTarget;
  }>;

  // Visual-level filters so the chart shows the same rows as the answer.
  filters?: AgentFilter[];

  // Shown as the visual's title.
  title?: string;
};

type ComponentData = {
  clientId: string;
  tenantId: string;
  redirectUri: string;
  reportId: string;
  pageId: string;
  visualRequest: (AgentVisualRequest & { requestId: string }) | null;
  // The account the app's Power BI queries use; offered in the chooser.
  loginHint?: string;
};

const POWERBI_SCOPE =
  "https://analysis.windows.net/powerbi/api/Report.ReadWrite.All";

const powerbi = new service.Service(
  factories.hpmFactory,
  factories.wpmpFactory,
  factories.routerFactory,
);

type PowerBIComponentInstance = {
  update: (data: ComponentData) => void;
  dispose: () => void;
};

const componentInstances = new WeakMap<
  HTMLElement | ShadowRoot,
  PowerBIComponentInstance
>();

const MyComponent: FrontendRenderer<Record<string, never>, ComponentData> = (
  args: FrontendRendererArgs<Record<string, never>, ComponentData>,
) => {
  const { parentElement, data } = args;
  const existingInstance = componentInstances.get(parentElement);

  if (existingInstance) {
    existingInstance.update(data);
    return existingInstance.dispose;
  }
  let currentData: ComponentData = data;

  const root = parentElement.querySelector<HTMLElement>(".component-root");
  const button = root?.querySelector<HTMLButtonElement>(".pbi-login");
  const status = root?.querySelector<HTMLElement>(".pbi-status");
  const reportContainer = root?.querySelector<HTMLElement>(".pbi-report");
  const switchButton = root?.querySelector<HTMLButtonElement>(".pbi-switch");

  if (!root || !button || !status || !reportContainer || !switchButton) {
    throw new Error("Power BI component HTML elements are missing.");
  }

  let disposed = false;
  let embeddedReport: Report | undefined;
  let msal: PublicClientApplication | undefined;
  const agentVisualNames = new Set<string>();
  // Track the last successfully processed AI visual request.
  let lastProcessedVisualRequestId: string | null = null;

  // Role names differ between visual types (a card has no "Y"). Use the
  // requested role when the visual has it, otherwise the closest one.
  const ROLE_ALTERNATIVES: Record<string, string[]> = {
    Category: ["Category", "Rows", "Axis", "Values"],
    Series: ["Series", "Legend", "Columns", "Values"],
    Y: ["Y", "Values", "Fields"],
    Y2: ["Y2", "Y", "Values"],
    Values: ["Values", "Fields", "Y"],
  };

  const resolveRole = async (
    visualType: string,
    wanted: string,
  ): Promise<string> => {
    try {
      const capabilities = await (embeddedReport as any).getVisualCapabilities(
        visualType,
      );
      const names: string[] = (capabilities?.dataRoles ?? []).map(
        (role: { name: string }) => role.name,
      );
      if (names.length === 0 || names.includes(wanted)) return wanted;
      for (const candidate of ROLE_ALTERNATIVES[wanted] ?? []) {
        if (names.includes(candidate)) return candidate;
      }
      return names[0];
    } catch {
      return wanted;
    }
  };

  const toPowerBIFilter = (filter: AgentFilter): models.IFilter => {
    if (filter.kind === "basic") {
      return {
        $schema: "http://powerbi.com/product/schema#basic",
        target: filter.target,
        operator: filter.operator,
        values: filter.values,
        filterType: models.FilterType.Basic,
      } as models.IBasicFilter;
    }
    if (filter.kind === "topN") {
      return {
        $schema: "http://powerbi.com/product/schema#topN",
        target: filter.target,
        operator: filter.operator,
        itemCount: filter.itemCount,
        orderBy: filter.orderBy,
        filterType: models.FilterType.TopN,
      } as unknown as models.IFilter;
    }
    return {
      $schema: "http://powerbi.com/product/schema#advanced",
      target: filter.target,
      logicalOperator: "And",
      conditions: [{ operator: filter.operator, value: filter.value }],
      filterType: models.FilterType.Advanced,
    } as unknown as models.IFilter;
  };

  const createAgentVisual = async (
    page: Awaited<ReturnType<Report["getPages"]>>[number],
    request: AgentVisualRequest,
  ): Promise<string> => {
    // The answer's chart fills the whole page: (0, 0) at the page's size.
    // This changes only the embedded view, never the saved report.
    // A missing or zero page size falls back to 1920 x 1080 (the AI Canvas
    // page as saved).
    const savedWidth = page.defaultSize?.width ?? 0;
    const savedHeight = page.defaultSize?.height ?? 0;
    const pageSize: { width: number; height: number } = {
      width: savedWidth > 0 ? savedWidth : 1920,
      height: savedHeight > 0 ? savedHeight : 1080,
    };
    const fullPage: models.IVisualLayout = {
      x: 0,
      y: 0,
      z: 1,
      width: pageSize.width,
      height: pageSize.height,
      displayState: { mode: models.VisualContainerDisplayMode.Visible },
    };
    console.log("AGENTIC PAGE SIZE:", {
      page: page.displayName,
      defaultSize: page.defaultSize,
      used: pageSize,
    });
    const response = await page.createVisual(request.visualType, fullPage);
    const visual = response.visual;

    try {
      const fields =
        request.fields ??
        (request.category && request.value
          ? [
              { role: "Category", target: request.category },
              { role: "Y", target: request.value },
            ]
          : []);

      if (fields.length === 0) {
        throw new Error("No data fields were supplied for the visual.");
      }

      for (const field of fields) {
        const role = await resolveRole(request.visualType, field.role);
        const fieldError = await visual.addDataField(role, field.target);

        if (fieldError) {
          throw new Error(JSON.stringify(fieldError));
        }
      }

      console.log("AGENTIC VISUAL CONFIGURED:", visual.name);
    } catch (error) {
      // Remove a partially configured visual.
      try {
        await page.deleteVisual(visual.name);
      } catch (cleanupError) {
        console.error("AGENTIC VISUAL CLEANUP FAILED:", cleanupError);
      }

      throw error;
    }

    // Filters and title are extras: if one fails the chart stays, and the
    // status line says the chart is unfiltered.
    if (request.filters && request.filters.length > 0) {
      try {
        await (visual as any).updateFilters(
          models.FiltersOperations.Replace,
          request.filters.map(toPowerBIFilter),
        );
        console.log("AGENTIC VISUAL FILTERS APPLIED:", request.filters.length);
      } catch (error) {
        console.error("AGENTIC VISUAL FILTERS FAILED:", error);
        showStatus(
          "Chart added, but its filters could not be applied: it shows unfiltered data.",
        );
      }
    }

    // Some visual types ignore the creation layout: move and resize again,
    // then log what the page reports.
    try {
      let placed = (await page.getVisuals()).find(
        (item) => item.name === visual.name,
      );
      const layout = placed?.layout;
      const off =
        !layout ||
        (layout.x ?? 0) !== 0 ||
        (layout.y ?? 0) !== 0 ||
        (layout.width ?? 0) < pageSize.width - 1 ||
        (layout.height ?? 0) < pageSize.height - 1;
      if (off) {
        await page.moveVisual(visual.name, 0, 0);
        await page.resizeVisual(visual.name, pageSize.width, pageSize.height);
        placed = (await page.getVisuals()).find(
          (item) => item.name === visual.name,
        );
      }
      console.log("AGENTIC VISUAL LAYOUT:", {
        visual: visual.name,
        visualType: request.visualType,
        pageSize,
        layout: placed?.layout,
      });
    } catch (error) {
      console.error("AGENTIC VISUAL LAYOUT FAILED:", error);
    }

    if (request.title) {
      try {
        await (visual as any).setProperty(
          { objectName: "title", propertyName: "visible" },
          { schema: "http://powerbi.com/product/schema#property", value: true },
        );
        await (visual as any).setProperty(
          { objectName: "title", propertyName: "titleText" },
          {
            schema: "http://powerbi.com/product/schema#property",
            value: request.title,
          },
        );
      } catch (error) {
        console.error("AGENTIC VISUAL TITLE FAILED:", error);
      }
    }

    return visual.name;
  };
  const removeAgentVisuals = async (
    page: Awaited<ReturnType<Report["getPages"]>>[number],
  ): Promise<void> => {
    for (const visualName of agentVisualNames) {
      try {
        await page.deleteVisual(visualName);
        console.log("AGENTIC VISUAL REMOVED:", visualName);
      } catch (error) {
        console.error("AGENTIC VISUAL REMOVAL FAILED:", visualName, error);
      }
      agentVisualNames.delete(visualName);
    }
  };

  const replaceAgentVisual = async (
    page: Awaited<ReturnType<Report["getPages"]>>[number],
    request: AgentVisualRequest,
  ): Promise<void> => {
    // On the dedicated "AI Canvas" page every existing visual is replaced,
    // including ones left by an earlier browser session. On any other page
    // only visuals this session created are removed.
    if ((page.displayName ?? "").trim().toLowerCase() === "ai canvas") {
      try {
        for (const existing of await page.getVisuals()) {
          agentVisualNames.add(existing.name);
        }
      } catch (error) {
        console.error("AGENTIC VISUAL LISTING FAILED:", error);
      }
    }

    // Create and configure the replacement first.
    const newVisualName = await createAgentVisual(page, request);

    // Remove only previously tracked agent visuals.
    try {
      await removeAgentVisuals(page);
    } finally {
      // Track the new visual even if old-visual cleanup fails.
      agentVisualNames.add(newVisualName);
    }

    console.log("AGENTIC VISUAL REPLACED:", newVisualName);
  };

  const processVisualRequest = async (
    request: ComponentData["visualRequest"],
  ): Promise<void> => {
    if (!request || !embeddedReport || disposed) return;

    if (request.requestId === lastProcessedVisualRequestId) {
      return;
    }

    const pages = await embeddedReport.getPages();
    const targetPage = pages.find((page) => page.name === currentData.pageId);

    if (!targetPage) {
      throw new Error("Target Power BI page was not found.");
    }

    await replaceAgentVisual(targetPage, request);

    lastProcessedVisualRequestId = request.requestId;

    console.log("AGENTIC VISUAL REQUEST PROCESSED:", request.requestId);
  };

  const showStatus = (message: string, warn = false) => {
    if (disposed) return;
    status.textContent = message;
    status.classList.toggle("pbi-warn", warn);
  };

  // The browser may already be signed in to a different Microsoft account
  // (for example one without Power BI). Never pick an account silently:
  // reuse only the account chosen earlier in this tab, otherwise open the
  // account chooser with the query account suggested.
  const sameAccount = (a?: string, b?: string) =>
    !!a && !!b && a.trim().toLowerCase() === b.trim().toLowerCase();

  // MSAL marks a sign-in popup as "in progress" in sessionStorage. A popup
  // lives only as long as the page that opened it, so a mark found when this
  // page first creates MSAL is left over from a reloaded page or a closed
  // popup, and would block every sign-in with "interaction_in_progress".
  const clearStaleInteraction = () => {
    try {
      for (const key of Object.keys(window.sessionStorage)) {
        if (key.startsWith("msal.") && key.endsWith("interaction.status")) {
          window.sessionStorage.removeItem(key);
        }
      }
    } catch (error) {
      console.warn("Could not clear the old sign-in state:", error);
    }
  };

  const isInteractionInProgress = (error: unknown) =>
    (error as { errorCode?: string })?.errorCode === "interaction_in_progress" ||
    String(error).includes("interaction_in_progress");

  const getAccessToken = async (chooseAccount = false): Promise<string> => {
    if (!msal) {
      clearStaleInteraction();
      msal = new PublicClientApplication({
        auth: {
          clientId: data.clientId,
          authority: `https://login.microsoftonline.com/${data.tenantId}`,
          redirectUri: `${window.location.origin}/app/static/auth.html`,
        },
        cache: {
          cacheLocation: "sessionStorage",
        },
      });

      await msal.initialize();
    }

    const active = msal.getActiveAccount();
    if (active && !chooseAccount) {
      try {
        const silent = await msal.acquireTokenSilent({
          scopes: [POWERBI_SCOPE],
          account: active,
        });
        if (silent.accessToken) return silent.accessToken;
      } catch (error) {
        if (!(error instanceof InteractionRequiredAuthError)) {
          console.warn("Silent Power BI sign-in failed:", error);
        }
      }
    }

    showStatus("Choose the Microsoft account that has Power BI...");

    const hint = currentData.loginHint || undefined;
    const request = {
      scopes: [POWERBI_SCOPE],
      prompt: "select_account",
      loginHint: chooseAccount ? undefined : hint,
    };
    let loginResult;
    try {
      loginResult = await msal.loginPopup(request);
    } catch (error) {
      // A sign-in left unfinished (popup closed, page reloaded): clear the
      // mark and try once more.
      if (!isInteractionInProgress(error)) throw error;
      clearStaleInteraction();
      loginResult = await msal.loginPopup(request);
    }

    if (!loginResult.accessToken) {
      throw new Error(
        "Microsoft sign-in did not return a Power BI access token.",
      );
    }

    msal.setActiveAccount(loginResult.account);

    return loginResult.accessToken;
  };

  const signedInAs = (): string =>
    msal?.getActiveAccount()?.username ?? "";

  const describeAccount = (): { text: string; warn: boolean } => {
    const user = signedInAs();
    const hint = currentData.loginHint;
    if (user && hint && !sameAccount(user, hint)) {
      return {
        text: `Signed in as ${user}; the app's answers use ${hint}.`,
        warn: true,
      };
    }
    return { text: user ? `Signed in as ${user}.` : "", warn: false };
  };

  const loadReport = async (chooseAccount = false) => {
    try {
      button.disabled = true;
      switchButton.disabled = true;
      showStatus("Authenticating with Microsoft Entra...");

      const accessToken = await getAccessToken(chooseAccount);
      switchButton.hidden = false;

      showStatus("Retrieving Power BI report details...");

      const response = await fetch(
        `https://api.powerbi.com/v1.0/myorg/reports/${data.reportId}`,
        {
          headers: {
            Authorization: `Bearer ${accessToken}`,
          },
        },
      );

      if (!response.ok) {
        const errorText = await response.text();
        if ([401, 403, 404].includes(response.status)) {
          throw new Error(
            `${signedInAs() || "This account"} cannot open this report ` +
              `(HTTP ${response.status}). Click "Use a different account" ` +
              "and pick the account that has Power BI.",
          );
        }
        throw new Error(`Power BI API error ${response.status}: ${errorText}`);
      }

      const reportDetails = await response.json();

      if (!reportDetails.embedUrl) {
        throw new Error("Power BI did not return an embed URL.");
      }

      showStatus("Embedding your interactive Power BI report...");

      powerbi.reset(reportContainer);

      const config: IEmbedConfiguration = {
        type: "report",
        id: data.reportId,
        embedUrl: reportDetails.embedUrl,
        accessToken,
        tokenType: models.TokenType.Aad,
        permissions: models.Permissions.ReadWrite,
        viewMode: models.ViewMode.Edit,
        settings: {
          // The AI Canvas page is saved with "Fit to page", which the
          // normal (Master) layout honours. A Custom layout is not used: it
          // can override where authored visuals are placed and whether they
          // show (2026-11-17: the answer's visual did not appear with it).
          layoutType: models.LayoutType.Master,
          // Filters, Visualizations and Data start collapsed so the chart
          // gets the room; each can still be opened from its edge tab.
          panes: {
            filters: { visible: true, expanded: false },
            visualizations: { visible: true, expanded: false },
            fields: { visible: true, expanded: false },
            pageNavigation: { visible: true },
          },
        },
      };

      embeddedReport = powerbi.embed(reportContainer, config) as Report;

      console.log("AGENTIC EMBED LAYOUT:", {
        layoutType: config.settings?.layoutType,
        panes: config.settings?.panes,
      });

      embeddedReport.on("loaded", async () => {
        if (disposed || !embeddedReport) return;
        try {
          const visualTypes = await (
            embeddedReport as any
          ).getAvailableVisualTypes();
          console.log("POWER BI AVAILABLE VISUAL TYPES:", visualTypes);
          const capabilities = await (
            embeddedReport as any
          ).getVisualCapabilities("clusteredColumnChart");
		  console.log(
		  "POWER BI COMBO CHART CAPABILITIES:",
		  await embeddedReport.getVisualCapabilities(
			"lineClusteredColumnComboChart"
		  )
		);
          console.log("POWER BI COLUMN CHART CAPABILITIES:", capabilities);
        } catch (error) {
          console.error("POWER BI AUTHORING TEST:", error);
        }

        try {
          const pages = await embeddedReport.getPages();
          const targetPage = pages.find((page) => page.name === data.pageId);

          if (targetPage) {
            await embeddedReport.setPage(data.pageId);
            const account = describeAccount();
            showStatus(
              `Power BI report loaded. ${account.text}`.trim(),
              account.warn,
            );

            console.log("AGENTIC REPORT READY FOR VISUAL REQUESTS");
			if (currentData.visualRequest) {
		  try {
			await processVisualRequest(currentData.visualRequest);
		  } catch (error) {
			console.error("AGENTIC INITIAL VISUAL REQUEST FAILED:", error);
		  }
		}
          } else {
            showStatus(
              "Report loaded. Requested page ID was not found; showing the default page.",
            );
          }
        } catch (error) {
          showStatus(
            `Report loaded, but page selection failed: ${String(error)}`,
          );
        }
      });

      embeddedReport.on("error", (event) => {
        const detail = event.detail;
        showStatus(`Power BI report error: ${JSON.stringify(detail)}`);
      });
    } catch (error) {
      const message = String(error);
      // AADSTS50020 / 50034 / 90072: the account is not in the tenant.
      const wrongTenant = /AADSTS(50020|50034|90072|700016)/.test(message);
      showStatus(
        wrongTenant
          ? 'This Microsoft account is not in the organisation that owns ' +
              'the report. Click "Use a different account".'
          : `Error: ${message}`,
        true,
      );
      switchButton.hidden = false;
    } finally {
      if (!disposed) {
        button.disabled = false;
        switchButton.disabled = false;
      }
    }
  };

  const onSignIn = () => void loadReport(false);
  const onSwitchAccount = () => void loadReport(true);

  const instance: PowerBIComponentInstance = {
    update: (newData: ComponentData) => {
      currentData = newData;

      if (newData.visualRequest) {
        void processVisualRequest(newData.visualRequest).catch((error) => {
          console.error("AGENTIC VISUAL REQUEST FAILED:", error);
        });
      }
    },

    dispose: () => {
      // Cleanup will be connected to the existing handler next.
    },
  };

  componentInstances.set(parentElement, instance);

  button.addEventListener("click", onSignIn);
  switchButton.addEventListener("click", onSwitchAccount);
  instance.dispose = () => {
    if (disposed) return;

    disposed = true;

    button.removeEventListener("click", onSignIn);
    switchButton.removeEventListener("click", onSwitchAccount);

    if (embeddedReport) {
      powerbi.reset(reportContainer);
      embeddedReport = undefined;
    }

    agentVisualNames.clear();
    componentInstances.delete(parentElement);

    console.log("AGENTIC COMPONENT DISPOSED");
  };

  return instance.dispose;
};

export default MyComponent;
