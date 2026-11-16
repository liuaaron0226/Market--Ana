import { handle } from "../lib/gate.js";
import marketPack from "./_market-pack.js";
import stockPack from "./_stock-pack.js";
import screensPack from "./_screens-pack.js";
import dataStatus from "./_data-status.js";
import comparePack from "./_compare-pack.js";

const deps = { fetchImpl: (...a) => fetch(...a), quoteFetch: (...a) => fetch(...a), overseasFetch: (...a) => fetch(...a), now: () => Date.now(), marketPack, stockPack, screensPack, dataStatus, comparePack };
export const onRequest = (context) => handle(context.request, context.env, () => context.next(), deps);
