import { describe, expect, it } from "vitest";
import shared from "@/test/days.json";
import { dayName, dayText, isoOf, parseDay } from "./dates";

describe("a day", () => {
  it.each(shared.days)("%s, on %s, reads as the server writes it: %s", (day, today, text) => {
    expect(dayText(day, today)).toBe(text);
  });

  it("parses only a real ISO day, at local midnight, and writes it back the same", () => {
    const day = parseDay(" 2026-10-23 ");
    expect(day && [day.getFullYear(), day.getMonth(), day.getDate(), day.getHours()]).toEqual([2026, 9, 23, 0]);
    expect(day && isoOf(day)).toBe("2026-10-23");
    for (const wrong of ["2026-02-30", "2026-13-01", "23/10/2026", "2026-10-3", "", "tomorrow"]) {
      expect(parseDay(wrong)).toBeUndefined();
    }
    const early = parseDay("0042-01-05");
    expect(early && isoOf(early)).toBe("0042-01-05");
  });

  it("says a day in full for a day's button", () => {
    expect(dayName(new Date(2026, 9, 23))).toBe("Friday 23 October 2026");
  });

  it("leaves a day it can't read as it came", () => {
    expect(dayText("someday", "2026-10-20")).toBe("someday");
  });
});
