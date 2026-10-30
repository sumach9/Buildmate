import { expect, test } from "vitest"; import { money } from "./api";
test("formats money", () => { expect(money("7537.5")).toBe("$7,537.50"); expect(money(null)).toBe("—"); });
