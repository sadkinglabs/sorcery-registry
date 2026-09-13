// The official API's published response contract for GET /api/cards,
// copied verbatim from https://api.sorcerytcg.com ("Card response schema")
// on 2026-09-13. Kept so a diff of their page is a diff of this file;
// schema/upstream-cards.schema.json is the registry's machine-checked
// reading of it (structure and types only - vocabularies stay open).

/**
 * The JSON shape returned by GET /api/cards.
 *
 * This contract deliberately has no imports from Prisma or another internal
 * package, so it can be copied directly into an API consumer.
 */
export type CardAPIDTO = {
  id: string;
  name: string;
  slug: string;
  engine: {
    type: "Avatar" | "Minion" | "Magic" | "Aura" | "Artifact" | "Site";
    category: "Avatar" | "Spell" | "Site" | "Token";
    rarity: "Unique" | "Elite" | "Exceptional" | "Ordinary" | null;
    slot: "Unique" | "Elite" | "Exceptional" | "Ordinary" | null;
    rules: string | null;
    cost: number | null;
    attack: number | null;
    defense: number | null;
    life: number | null;
    water: number | null;
    earth: number | null;
    fire: number | null;
    air: number | null;
    elements: ("Earth" | "Fire" | "Water" | "Air" | "None")[];
    subtypes: (
      | "Angel"
      | "Beast"
      | "Demon"
      | "Dragon"
      | "Dwarf"
      | "Faerie"
      | "Giant"
      | "Gnome"
      | "Goblin"
      | "Merfolk"
      | "Monster"
      | "Mortal"
      | "Ogre"
      | "Spirit"
      | "Sphinx"
      | "Troll"
      | "Undead"
      | "Armor"
      | "Automaton"
      | "Device"
      | "Document"
      | "Instruments"
      | "Monument"
      | "Potion"
      | "Relic"
      | "Weapon"
      | "Desert"
      | "River"
      | "Tower"
      | "Village"
    )[];
    keywords: (
      | "Airborne"
      | "Burrowing"
      | "Charge"
      | "Deathrite"
      | "Disabled"
      | "Flood"
      | "Genesis"
      | "Immobile"
      | "Lance"
      | "Landbound"
      | "Lethal"
      | "Movement"
      | "Ranged"
      | "Spellcaster"
      | "Stealth"
      | "Submerge"
      | "Voidwalk"
      | "Ward"
      | "Waterbound"
    )[];
    umbrellas: ("Knight" | "Royalty" | "Evil")[];
    back: {
      type: "Avatar" | "Minion" | "Magic" | "Aura" | "Artifact" | "Site";
      category: "Avatar" | "Spell" | "Site" | "Token";
      rarity: "Unique" | "Elite" | "Exceptional" | "Ordinary" | null;
      slot: "Unique" | "Elite" | "Exceptional" | "Ordinary" | null;
      rules: string | null;
      cost: number | null;
      attack: number | null;
      defense: number | null;
      life: number | null;
      water: number | null;
      earth: number | null;
      fire: number | null;
      air: number | null;
      elements: ("Earth" | "Fire" | "Water" | "Air" | "None")[];
      subtypes: (
        | "Angel"
        | "Beast"
        | "Demon"
        | "Dragon"
        | "Dwarf"
        | "Faerie"
        | "Giant"
        | "Gnome"
        | "Goblin"
        | "Merfolk"
        | "Monster"
        | "Mortal"
        | "Ogre"
        | "Spirit"
        | "Sphinx"
        | "Troll"
        | "Undead"
        | "Armor"
        | "Automaton"
        | "Device"
        | "Document"
        | "Instruments"
        | "Monument"
        | "Potion"
        | "Relic"
        | "Weapon"
        | "Desert"
        | "River"
        | "Tower"
        | "Village"
      )[];
      keywords: (
        | "Airborne"
        | "Burrowing"
        | "Charge"
        | "Deathrite"
        | "Disabled"
        | "Flood"
        | "Genesis"
        | "Immobile"
        | "Lance"
        | "Landbound"
        | "Lethal"
        | "Movement"
        | "Ranged"
        | "Spellcaster"
        | "Stealth"
        | "Submerge"
        | "Voidwalk"
        | "Ward"
        | "Waterbound"
      )[];
      umbrellas: ("Knight" | "Royalty" | "Evil")[];
    } | null;
  };
  printings: {
    id: string;
    slug: string;
    /** ISO 8601 date-time string. */
    printedAt: string;
    set: {
      name: string;
      code: string;
      /** ISO 8601 date-time string. */
      releasedAt: string;
    };
    meta: {
      finish: "Standard" | "Foil" | "Rainbow";
      product:
        | "Booster"
        | "BoxTopper"
        | "PreconstructedDeck"
        | "Dust"
        | "DraftKit"
        | "WelcomeKit"
        | "Kickstarter"
        | "TeamCovenant"
        | "AlphaInvestments"
        | "StarCityGames"
        | "OrganizedPlay";
      typeline: string;
      flavor: string | null;
      artist: { name: string; slug: string };
      back: {
        finish: "Standard" | "Foil" | "Rainbow";
        product:
          | "Booster"
          | "BoxTopper"
          | "PreconstructedDeck"
          | "Dust"
          | "DraftKit"
          | "WelcomeKit"
          | "Kickstarter"
          | "TeamCovenant"
          | "AlphaInvestments"
          | "StarCityGames"
          | "OrganizedPlay";
        typeline: string;
        flavor: string | null;
        artist: { name: string; slug: string };
      } | null;
    };
  }[];
};
