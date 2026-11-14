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

type AgentVisualRequest = {
  visualType: string;

  // Existing single-measure requests remain supported.
  category?: models.IColumnTarget;
  value?: models.IMeasureTarget;

  // Advanced visuals can assign multiple fields to different roles.
  fields?: Array<{
    role: string;
    target: models.IColumnTarget | models.IMeasureTarget;
  }>;
};

type ComponentData = {
  clientId: string;
  tenantId: string;
  redirectUri: string;
  reportId: string;
  pageId: string;
  visualRequest: (AgentVisualRequest & { requestId: string }) | null;
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

  if (!root || !button || !status || !reportContainer) {
    throw new Error("Power BI component HTML elements are missing.");
  }

  let disposed = false;
  let embeddedReport: Report | undefined;
  let msal: PublicClientApplication | undefined;
  const agentVisualNames = new Set<string>();
  // Track the last successfully processed AI visual request.
  let lastProcessedVisualRequestId: string | null = null;

  const createAgentVisual = async (
    page: Awaited<ReturnType<Report["getPages"]>>[number],
    request: AgentVisualRequest,
  ): Promise<string> => {
    const response = await page.createVisual(request.visualType);
    const visual = response.visual;

    try {
          const fields = request.fields ?? (
      request.category && request.value
        ? [
            { role: "Category", target: request.category },
            { role: "Y", target: request.value },
          ]
        : []
    );

    if (fields.length === 0) {
      throw new Error("No data fields were supplied for the visual.");
    }

    for (const field of fields) {
      const fieldError = await visual.addDataField(
        field.role,
        field.target,
      );

      if (fieldError) {
        throw new Error(JSON.stringify(fieldError));
      }
    }

      console.log("AGENTIC VISUAL CONFIGURED:", visual.name);
      return visual.name;
    } catch (error) {
      // Remove a partially configured visual.
      try {
        await page.deleteVisual(visual.name);
      } catch (cleanupError) {
        console.error("AGENTIC VISUAL CLEANUP FAILED:", cleanupError);
      }

      throw error;
    }
  };
  const removeAgentVisuals = async (
    page: Awaited<ReturnType<Report["getPages"]>>[number],
  ): Promise<void> => {
    for (const visualName of agentVisualNames) {
      await page.deleteVisual(visualName);
      agentVisualNames.delete(visualName);
      console.log("AGENTIC VISUAL REMOVED:", visualName);
    }
  };

  const replaceAgentVisual = async (
    page: Awaited<ReturnType<Report["getPages"]>>[number],
    request: AgentVisualRequest,
  ): Promise<void> => {
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

  const showStatus = (message: string) => {
    if (!disposed) status.textContent = message;
  };

  const getAccessToken = async (): Promise<string> => {
    if (!msal) {
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

    showStatus("Opening Microsoft sign-in...");

    console.log("MSAL WINDOW DIAGNOSTIC:", {
      url: window.location.href,
      name: window.name,
      hasOpener: !!window.opener,
      isIframe: window.parent !== window,
    });

    const loginResult = await msal.loginPopup({
      scopes: [POWERBI_SCOPE],
    });

    if (!loginResult.accessToken) {
      throw new Error(
        "Microsoft sign-in did not return a Power BI access token.",
      );
    }

    msal.setActiveAccount(loginResult.account);

    return loginResult.accessToken;
  };

  const loadReport = async () => {
    try {
      button.disabled = true;
      showStatus("Authenticating with Microsoft Entra...");

      const accessToken = await getAccessToken();

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
          panes: {
            filters: { visible: true },
            pageNavigation: { visible: true },
          },
        },
      };

      embeddedReport = powerbi.embed(reportContainer, config) as Report;

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
            showStatus("Power BI report loaded.");

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
      showStatus(`Error: ${String(error)}`);
    } finally {
      if (!disposed) button.disabled = false;
    }
  };

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

  button.addEventListener("click", loadReport);
  instance.dispose = () => {
    if (disposed) return;

    disposed = true;

    button.removeEventListener("click", loadReport);

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
