import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("App", () => {
  it("renders the application shell", () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "Carrier Pool" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("No demo load selected.");
  });
});
