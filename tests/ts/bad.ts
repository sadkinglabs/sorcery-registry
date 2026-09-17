// Two misuses the declarations must reject; tsc is expected to report
// exactly two errors here. If it reports fewer, the types went loose.
import type { Card } from "../../schema/registry";
import sample from "./sample";
type Mutable<T> = T extends readonly (infer U)[] ? Mutable<U>[] : T extends object ? { -readonly [K in keyof T]: Mutable<T[K]> } : T;
const first: Card = (sample as unknown as Mutable<typeof sample>).cards[0]!;
const wrong: Card = { ...first, cost: "3" };   // cost is number | null
const gone = first.image_urls?.medium;         // no such rendition
console.log(wrong, gone);
