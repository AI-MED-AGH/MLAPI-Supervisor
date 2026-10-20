import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useApprovals } from "../api/hooks";
import { useAuth } from "../auth";
import { Button, cx } from "./ui";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/models", label: "Models" },
  { to: "/approvals", label: "Approvals", badge: true },
  { to: "/keys", label: "API keys" },
  { to: "/observers", label: "Observers" },
];

function useTheme() {
  const [dark, setDark] = useState(() => {
    try {
      const saved = localStorage.getItem("theme");
      if (saved) return saved === "dark";
    } catch {
      /* storage blocked */
    }
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
  });
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try {
      localStorage.setItem("theme", dark ? "dark" : "light");
    } catch {
      /* storage blocked */
    }
  }, [dark]);
  return [dark, () => setDark((d) => !d)] as const;
}

export function Layout() {
  const { logout } = useAuth();
  const [dark, toggle] = useTheme();
  const [open, setOpen] = useState(false);
  const pending = useApprovals("pending").data?.length ?? 0;

  const nav = (
    <nav aria-label="Main" className="flex flex-col gap-1">
      {NAV.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          onClick={() => setOpen(false)}
          className={({ isActive }) =>
            cx(
              "flex items-center justify-between rounded-lg px-3 py-2 text-sm font-medium",
              isActive
                ? "bg-brand-50 text-brand-700 dark:bg-brand-600/20 dark:text-brand-100"
                : "text-slate-700 hover:bg-slate-200/60 dark:text-slate-300 dark:hover:bg-slate-800",
            )
          }
        >
          {item.label}
          {item.badge && pending > 0 && (
            <span aria-label={`${pending} pending`} className="rounded-full bg-amber-500 px-2 text-xs font-semibold text-white">
              {pending}
            </span>
          )}
        </NavLink>
      ))}
    </nav>
  );

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[15rem_1fr]">
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3 lg:hidden dark:border-slate-800 dark:bg-slate-900">
        <span className="font-semibold">MLAPI Supervisor</span>
        <Button aria-expanded={open} aria-controls="sidebar" onClick={() => setOpen((o) => !o)}>
          Menu
        </Button>
      </header>
      <aside
        id="sidebar"
        className={cx(
          "flex-col gap-6 border-r border-slate-200 bg-white p-4 lg:sticky lg:top-0 lg:flex lg:h-screen dark:border-slate-800 dark:bg-slate-900",
          open ? "flex" : "hidden",
        )}
      >
        <div className="hidden items-center gap-2 px-2 pt-1 lg:flex">
          <span className="grid size-8 place-items-center rounded-lg bg-brand-600 text-sm font-bold text-white">M</span>
          <span className="font-semibold">MLAPI Supervisor</span>
        </div>
        {nav}
        <div className="mt-auto flex flex-col gap-2">
          <Button variant="ghost" onClick={toggle} aria-pressed={dark}>
            {dark ? "Light theme" : "Dark theme"}
          </Button>
          <Button variant="ghost" onClick={() => void logout()}>
            Sign out
          </Button>
        </div>
      </aside>
      <main className="min-w-0 px-4 py-6 sm:px-8 sm:py-8">
        <div className="mx-auto max-w-6xl">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
