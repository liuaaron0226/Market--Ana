import { handle } from "../lib/gate.js";

export const onRequest = (context) => handle(context.request, context.env, () => context.next());
