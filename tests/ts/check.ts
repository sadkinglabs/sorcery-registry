// The generated declarations against real data. sample.ts is written by
// `python -m registry.types --sample tests/ts/sample.ts` (CI does; it is
// not committed): a slice of the export `as const`, so every value keeps
// its literal type. Assigning it to the declarations is the structural
// check; the readonly that `as const` adds is stripped first, because a
// consumer's records are mutable.
import type { Registry, Card, Printing, Face } from "../../schema/registry";
import sample from "./sample";

type Mutable<T> = T extends readonly (infer U)[] ? Mutable<U>[] : T extends object ? { -readonly [K in keyof T]: Mutable<T[K]> } : T;

const registry: Registry = sample as unknown as Mutable<typeof sample>;
const first: Card = registry.cards[0]!;
const print: Printing = registry.printings[0]!;

// A Card and a history row are usable wherever a Face is.
function power(face: Face): number | null { return face.power; }
power(first);
power(registry.card_history[0]!);

// The enum narrows.
const status: "missing" | "lowres" | "ok" = print.image_status;
console.log(first.name, print.printing_id, status);
