import { handle } from "../lib/gate.js";
import marketPack from "./_market-pack.js";
import stockPack from "./_stock-pack.js";

const deps = { fetchImpl: (...a) => fetch(...a), now: () => Date.now(), marketPack, stockPack };
export const onRequest = (context) => handle(context.request, context.env, () => context.next(), deps);
