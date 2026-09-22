// Sorcery Card Registry export: TypeScript declarations for registry.json.
// Generated from schema.json by `python -m registry.types`; do not edit.
// Usage:  import type { Registry, Card, Printing } from "./types";
//         const registry: Registry = JSON.parse(text);
// Every field carries the schema's description as JSDoc. A field's
// absence from `required` reads as optional; nothing here is optional
// today. Objects declare no index signature: the schema allows no
// fields beyond the ones listed.

/** Permanent card-level id, shared by every printing of the card. */
export type CodexId = string;

/** Permanent id of one physical print (set + product + finish). */
export type PrintingId = string;

export type IsoDate = string;

export type Threshold = number;

/**
 * A set's code: a label, never a number, so nothing about a set is read from it (what a set is, is
 * its kind). The publisher's codes are three digits (001 Alpha, 002 Beta, 004 Arthurian Legends,
 * 005 Dragonlord, 006 Gothic, 999 Promo; 003 is deliberately unused). The registry's own are three
 * capital letters, such as CUR for the curios, so they can never collide with the publisher's.
 * There is no collector number: cards have no official serialisation within a set.
 */
export type SetCode = string;

/** The gameplay data of one face of a card. */
export interface Face {
  /** Observed values: Artifact, Aura, Avatar, Magic, Minion, Site. */
  type: string | null;
  /** Coarser grouping: Spell (Minion, Magic, Artifact, Aura), Site, Avatar, Token. */
  category: string | null;
  /**
   * The rarity printed on the card. Observed values: Ordinary, Exceptional, Elite, Unique; null on
   * Avatars and some tokens.
   */
  rarity: string | null;
  /**
   * The rarity slot the card is distributed in. Agrees with rarity except for Avatars (no printed
   * rarity) and tokens.
   */
  slot: string | null;
  /** In upstream's order, e.g. ["Beast", "Spirit"]. */
  subtypes: string[];
  /** In upstream's order. ["None"] means colourless (Artifacts, Avatars, most Sites). */
  elements: string[];
  /**
   * Ability keywords the card carries (Airborne, Genesis, Spellcaster, ...), as upstream tags
   * them.
   */
  keywords: string[];
  /** Cross-subtype groupings rules text refers to (Evil, Knight, Royalty). */
  umbrellas: string[];
  /** Mana cost. null for Sites and Avatars, and for X costs. */
  cost: number | null;
  /** Printed attack; null when the card has none. */
  attack: number | null;
  /** Printed defense; null when the card has none. */
  defense: number | null;
  /**
   * Derived: Sorcery's power - equal to attack when attack equals defense, otherwise floor((attack
   * + defense) / 2); null when either is null. Published so every consumer computes the same
   * number.
   */
  power: number | null;
  /** Only Avatars have a life value. */
  life: number | null;
  /** Air threshold: the number of Air the card needs in play to be cast. 0 when none. */
  thr_air: Threshold;
  /** Earth threshold. 0 when none. */
  thr_earth: Threshold;
  /** Fire threshold. 0 when none. */
  thr_fire: Threshold;
  /** Water threshold. 0 when none. */
  thr_water: Threshold;
  /**
   * Current text the card plays by, applying to every printing. Canonicalised: \n line endings, no
   * trailing whitespace, one line per ability. Changes are recorded in rules_history.
   */
  rules_text: string;
}

/** The physical facts that differ per face of a double-faced printing. */
export interface PrintingFace {
  /** The artist as credited; null when unrecorded. */
  artist: string | null;
  /** Upstream's slug for the artist, e.g. jeff_a_menges. */
  artist_slug: string | null;
  /** The flavour text as printed; empty or null when the card has none. */
  flavour_text: string | null;
  /** The flavour typeline printed under the name, e.g. 'An Ordinary Mortal new to power'. */
  typeline: string | null;
  /** Derived: the back face's own image renditions; null while the registry holds none. */
  image_urls: ImageUrls | null;
}

/**
 * Addresses of one face's image renditions. Object names are self-describing -
 * {printing_id}.{key}.{rendition}.{ext}, '.back' before the rendition for a back face - where key
 * is the art-version key: it changes when the art or the encoding recipe changes, so the bytes at
 * a name never change. Card content and images are (c) Erik's Curiosa, served for archive,
 * identification and site function.
 */
export interface ImageUrls {
  /** 146x204 WebP (aspect preserved, so possibly a pixel narrower): search results, lists. */
  small: string;
  /** 488x680 WebP: card pages. */
  normal: string;
  /** 672x936 WebP: click-through. */
  large: string;
  /** The publisher's file, untouched. */
  original: string;
}

/**
 * missing: the registry holds no image for this face (image_urls null). lowres: held and served in
 * every rendition, but the publisher's file was too small for the large rendition and was upscaled
 * (the early sets ship at 380x531). ok: held at full size.
 */
export type ImageStatus = "missing" | "lowres" | "ok";

/**
 * Something known about a record that the official API does not say, with where it came from. A
 * note never contradicts a field: a fact that fits a field is a correction and changes the field
 * instead.
 */
export interface Note {
  /** The fact, in plain words. */
  text: string;
  /**
   * Where the fact came from: a document, a product, a community report. Names a place, not a
   * person, unless that person asked to be credited.
   */
  source: string;
  /** The day the note was written down, not the day the fact became true. */
  recorded: IsoDate;
}

/**
 * Who stands behind the record. 'api': the official API serves it. 'manual': the registry recorded
 * it by hand (data/manual.json) because the API does not serve it. A manual record becomes 'api',
 * keeping its id, once the API serves it and a person confirms the match.
 */
export type Origin = "api" | "manual";

/**
 * Null for a record the registry only ever observed in the official API. For a record the registry
 * recorded by hand: where it came from and when, kept after the API starts serving it.
 */
export type Manual = null | {
  /**
   * Where the hand record came from. Names a place, not a person, unless that person asked to be
   * credited.
   */
  source: string;
  /** The day the record was written down. */
  recorded: IsoDate;
  /**
   * The day the official API was confirmed to serve it, after which origin is 'api'; null while it
   * is still manual.
   */
  confirmed_at: IsoDate | null;
  /**
   * Set when a manual record was found to be wrong. Ids are permanent, so it stays, marked; a
   * withdrawn printing is never a card's default.
   */
  withdrawn: null | {
    /** The day the record was withdrawn. */
    on: IsoDate;
    /** Why the record was found to be wrong. */
    reason: string;
  };
};

/**
 * Counts match the section lengths. Deliberately no timestamp: an unchanged registry produces a
 * byte-identical file.
 */
export interface Header {
  /**
   * The shape of the file. Goes up when a field is added; a breaking change is a new major release
   * instead.
   */
  schema_version: number;
  /** The official API the data was read from. */
  source: string;
  /** How many records the sets section holds. */
  sets: number;
  /** How many records the cards section holds. */
  cards: number;
  /** How many records the printings section holds. */
  printings: number;
  /** How many rows the slug_history section holds. */
  slug_history: number;
  /** How many rows the name_history section holds. */
  name_history: number;
  /** How many rows the card_history section holds. */
  card_history: number;
}

/** Derived catalogue of the sets themselves, with distinct-card and printing counts. */
export interface RegistrySet {
  /** The official three-digit code. A label, not an order: 003 is unused. */
  set_code: SetCode | null;
  /** The official display name. */
  set_name: string;
  /** Derived: the earliest released_at among the set's printings. */
  released_at: IsoDate | null;
  /** Distinct cards in the set. */
  cards: number;
  /** Printings in the set. */
  printings: number;
  /**
   * What the set is, as the registry records it (data/sets.json); never read from the code.
   * release: a set release, whose printings came out with it. promo: the publisher's bucket for
   * promos (999), which came out with many releases; each printing's released_with says which.
   * registry: a set of the registry's own for printings the official API will never serve, such as
   * CUR.
   */
  kind: "release" | "promo" | "registry";
  /**
   * 'manual' when every printing in the set is a manual record, as for a set code of the
   * registry's own; 'api' otherwise.
   */
  origin: Origin;
  /** Derived: this set's JSON object on api.kairosarchive.net; null only for a set without a code. */
  api_url: string | null;
  /** Derived: this set's page on kairosarchive.net. */
  kairos_url: string | null;
}

/** One record per card as a game piece; reprints share one record. */
export interface Card extends Face {
  /** The card's permanent ID. */
  codex_id: CodexId;
  /** The current name. Earlier names are in name_history. */
  name: string;
  /**
   * The back face of a double-faced card; null for every other card. The card's own fields are its
   * front.
   */
  back: Face | null;
  /**
   * Registry-owned: true once any gameplay field of the card (type, elements, cost, attack,
   * defense, life, thresholds, rules_text, back) has changed since it was printed. Seeded from the
   * old upstream UPDATED: marker; set whenever a sync observes such a change (see card_history);
   * corrected only through data/overrides.json. Rarity, slot, subtypes, keywords and umbrellas are
   * classification, so a change to them is not errata.
   */
  errata: boolean;
  /**
   * Derived, sorted: the sets this card appears in. For products and finishes, follow
   * printing_ids.
   */
  set_codes: SetCode[];
  /** Derived, sorted, and it only ever grows. */
  printing_ids: PrintingId[];
  /**
   * Derived: the representative printing, by a fixed rule so every consumer picks the same one -
   * not retired, showing the card's current face (printed_as_current) over older values, Booster
   * over other products, Standard over other finishes, most recently released, lowest id. A
   * reprint that changed the card's stats therefore becomes the default even when it is a promo;
   * otherwise a promo never outranks a Booster printing. A registry maintainer can pin a different
   * printing through data/overrides.json, with a reason. null only for a card with no printings.
   */
  default_printing_id: PrintingId | null;
  /**
   * Derived: this card's JSON object on api.kairosarchive.net, under the moving major alias (/v3/
   * always redirects to the newest verified v3.x release), so the address leads to the current
   * record from any copy.
   */
  api_url: string;
  /** Derived: this card's page on kairosarchive.net. */
  kairos_url: string;
  /**
   * Derived: the image of the card's default_printing_id (its front face), null while that
   * printing has no image.
   */
  image_urls: ImageUrls | null;
  /** Derived: the default printing's image_status. */
  image_status: ImageStatus;
  origin: Origin;
  manual: Manual;
  /**
   * What the registry knows about this card that the official API does not say, oldest first.
   * Empty for most records.
   */
  notes: Note[];
}

/**
 * One record per physical print: a specific set, product and finish. Gameplay data is on the card
 * it points at.
 */
export interface Printing {
  /** This printing's permanent ID. */
  printing_id: PrintingId;
  /** The card this is a printing of. */
  codex_id: CodexId;
  /** Derived from the card record so a printing is readable without a join. */
  card_name: string;
  /** The set's official display name. */
  set_name: string;
  /** The set's official three-digit code. */
  set_code: SetCode | null;
  /** The date this printing reached the public. */
  released_at: IsoDate | null;
  /**
   * The set release this printing belongs to: a set whose kind is release. For a printing in a
   * release set it is that set. For a promo (a set of kind promo, 999) or a curio (kind registry,
   * CUR), filed outside any release, it is the release it came out with, recorded by hand; null
   * until recorded.
   */
  released_with: SetCode | null;
  /**
   * Official product line, spelled as upstream spells it. Observed values: Booster, BoxTopper,
   * Dust, OrganizedPlay, PreconstructedDeck, DraftKit, AlphaInvestments, WelcomeKit, Kickstarter,
   * TeamCovenant, StarCityGames.
   */
  product: string | null;
  /** Observed values: Standard, Foil, Rainbow. */
  finish: string | null;
  /** Current official API slug. Mutable - never key on it; resolve old ones via slug_history. */
  slug: string;
  /** The artist as credited; null when unrecorded. */
  artist: string | null;
  /** Upstream's slug for the artist, e.g. jeff_a_menges. */
  artist_slug: string | null;
  /** The flavour text as printed; empty or null when the card has none. */
  flavour_text: string | null;
  /** The flavour typeline printed under the name, e.g. 'An Ordinary Mortal new to power'. */
  typeline: string | null;
  /** Back face of a double-faced printing; null otherwise. */
  back: PrintingFace | null;
  /**
   * The art-version key of this printing's front image - sha256(original bytes || encoding
   * recipe)[:12], recorded in data/images.json by the image pipeline; null while the registry
   * holds none. image_urls is derived from it; a new key means new art or a new recipe, never
   * changed bytes at an old address.
   */
  image_hash: string | null;
  /**
   * Derived: whether the values physically printed on this printing equal the card's current
   * gameplay face - true when the printing was released on or after the current face took effect,
   * or entered the registry on or after it (a reprint that arrives in the same sync as the change
   * it carries), or when the card's face has never changed. null when the printing has no release
   * date and predates the current face, or when it shows no face at all (a textless promo listed
   * under unknown_printings in data/errata.json).
   */
  printed_as_current: boolean | null;
  /** Set when the printing vanished upstream. Retired printings keep their rows and ids forever. */
  retired_at: IsoDate | null;
  /** Derived: this printing's JSON object on api.kairosarchive.net, under the moving major alias. */
  api_url: string;
  /** Derived: this printing's page on kairosarchive.net. */
  kairos_url: string;
  /** Derived: the front face's image renditions, null while the registry holds none. */
  image_urls: ImageUrls | null;
  /** How good the source of the front image was: missing, lowres or ok. */
  image_status: ImageStatus;
  origin: Origin;
  manual: Manual;
  /**
   * What the registry knows about this printing that the official API does not say, oldest first.
   * Empty for most records.
   */
  notes: Note[];
}

/** Every slug that has ever existed, mapped to its printing. valid_to null = the current slug. */
export interface SlugHistoryRow {
  /** The slug, exactly as the official API issued it. */
  slug: string;
  /** The printing it belonged to. A slug belongs to one printing, ever. */
  printing_id: PrintingId;
  /** The first date the registry saw this slug. */
  valid_from: IsoDate;
  /** The date it was replaced; null while it is the current slug. */
  valid_to: IsoDate | null;
}

/**
 * Every name a card has ever carried. valid_to null = the current name. Unlike slugs, a name is
 * not owned: two cards may hold the same name at different times.
 */
export interface NameHistoryRow {
  /** The name, exactly as the official API gave it. */
  name: string;
  /** The card that carried the name. */
  codex_id: CodexId;
  /** The first date the registry saw this name. */
  valid_from: IsoDate;
  /** The date it was replaced; null while it is the current name. */
  valid_to: IsoDate | null;
}

/**
 * Every state a card's gameplay face has been in, as observed by syncs: the face's fields
 * flattened into the row. valid_to null = the current face, which equals the card record. A closed
 * row is how the card played until valid_to; a printing released within a row's dates was printed
 * with that row's values. Seeded with every card's face on 2026-09-09.
 */
export interface CardHistoryRow extends Face {
  /** The card this face belongs to. */
  codex_id: CodexId;
  /** Start of the range this face was in force. What the date means depends on source. */
  valid_from: IsoDate;
  /** End of the range; null for the current face. */
  valid_to: IsoDate | null;
  /**
   * Where the face came from: 'api' when the registry observed it in the official API; 'card' when
   * it was recorded by hand from what is printed on the card (data/errata.json), the case of a
   * card whose printed text differs from the text the API serves; 'manual' for the face of a card
   * the registry recorded by hand because the API does not serve it (data/manual.json).
   */
  source: "api" | "card" | "manual";
  /** The back face at that time; null unless the card is double-faced. */
  back: Face | null;
}

/**
 * Stable identifiers for every card and printing in Sorcery: Contested Realm. codex_id (C000042)
 * identifies a card across all its reprints; printing_id (P000042) identifies one physical print.
 * Key on those two - every other field is data that can change. The shape mirrors the official
 * API: gameplay data lives on the card, physical facts on the printing. This schema is structural:
 * vocabularies noted in descriptions (product, finish, type, rarity, category) are the values
 * observed today, not closed sets, so new official values never become schema violations.
 */
export interface Registry {
  /**
   * Counts match the section lengths. Deliberately no timestamp: an unchanged registry produces a
   * byte-identical file.
   */
  header: Header;
  sets: RegistrySet[];
  cards: Card[];
  printings: Printing[];
  slug_history: SlugHistoryRow[];
  name_history: NameHistoryRow[];
  card_history: CardHistoryRow[];
}
