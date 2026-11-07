import { handle } from "../lib/gate.js";
import marketPack from "./_market-pack.js";

const deps = { fetchImpl: (...a) => fetch(...a), now: () => Date.now(), marketPack };
export const onRequest = (context) => handle(context.request, context.env, () => context.next(), deps);
