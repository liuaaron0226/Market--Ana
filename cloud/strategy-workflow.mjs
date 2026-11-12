// Offline authorization only: no credentials, remote commands, or strategy calculation.
import { appendFileSync } from "node:fs";
import { strategyWorkflowPolicy } from "./lib/release-policy.js";

const decision = strategyWorkflowPolicy(process.env);
if (!process.env.GITHUB_OUTPUT) throw new Error("GITHUB_OUTPUT is required");
appendFileSync(process.env.GITHUB_OUTPUT,
  `allowed=${decision.allowed}\ndry_run=${decision.dry_run}\n`, "utf8");
