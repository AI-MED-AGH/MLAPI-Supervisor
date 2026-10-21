import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useApprovals } from "../api/hooks";
import { useAuth } from "../auth";
import { applyTheme, storedTheme } from "../lib/theme";
import { Button, cx, Logo } from "./ui";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/models", label: "Models" },
  { to: "/approvals", label: "Approvals", badge: true },
  { to: "/keys", label: "API keys" },
  { to: "/observers", label: "Observers" },
];

function Account({ className, dark, toggle, logout }: { className?: string; dark: boolean; toggle: () => void; logout: () => Promise<void> }) {
  return (
    <div className={cx("flex items-center gap-1", className)}>
      <Button variant="ghost" onClick={toggle} aria-pressed={dark} className="px-2.5">
        {dark ? "Light theme" : "Dark theme"}
      </Button>
      <Button variant="ghost" onClick={() => void logout()} className="px-2.5">
        Sign out
      </Button>
    </div>
  );
}

function useTheme() {
  const [dark, setDark] = useState(() => storedTheme() === "dark");
  useEffect(() => applyTheme(dark ? "dark" : "light"), [dark]);
  return [dark, () => setDark((d) => !d)] as const;
}

export function Layout() {
  const { logout } = useAuth();
  const [dark, toggle] = useTheme();
  const [open, setOpen] = useState(false);
  const { pathname } = useLocation();
  const pending = useApprovals("pending").data?.length ?? 0;

  return (
    <>
      <a
        href="#content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:rounded-md focus:bg-ink focus:px-3 focus:py-2 focus:text-sm focus:text-canvas"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-30 border-b border-hairline bg-canvas/80 backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-8 px-4 sm:px-8">
          <Link to="/" className="flex h-16 items-center gap-3 rounded-md">
            <Logo className="size-8" />
            <span className="text-[15px] font-semibold tracking-[-0.02em]">
              MLAPI <span className="font-normal text-mute">Supervisor</span>
            </span>
          </Link>

          <nav id="main-nav" aria-label="Main" className={cx("order-last w-full flex-col pb-3 md:order-none md:flex md:w-auto md:flex-row md:gap-1 md:pb-0", open ? "flex" : "hidden")}>
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                onClick={() => setOpen(false)}
                className={({ isActive }) =>
                  cx(
                    "relative flex items-center gap-2 px-3 py-2.5 text-sm font-medium transition-colors duration-150 md:h-16 md:py-0",
                    "after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:origin-left after:bg-ink after:transition-transform after:duration-200 max-md:after:hidden",
                    isActive ? "text-ink after:scale-x-100" : "text-mute after:scale-x-0 hover:text-ink",
                  )
                }
              >
                {item.label}
                {item.badge && pending > 0 && (
                  <span aria-label={`${pending} pending`} className="grid min-w-5 place-items-center rounded-sm bg-ink px-1 font-mono text-[11px] leading-5 font-semibold text-canvas tabular-nums">
                    {pending}
                  </span>
                )}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-1">
            <Account className="max-md:hidden" dark={dark} toggle={toggle} logout={logout} />
            <Button variant="ghost" className="px-2.5 md:hidden" aria-expanded={open} aria-controls="main-nav" onClick={() => setOpen((o) => !o)}>
              Menu
            </Button>
          </div>
          {open && <Account className="order-last w-full border-t border-hairline py-2 md:hidden" dark={dark} toggle={toggle} logout={logout} />}
        </div>
      </header>
      <main id="content" className="mx-auto max-w-6xl px-4 pt-12 pb-24 sm:px-8 sm:pt-16">
        <div key={pathname} className="page">
          <Outlet />
        </div>
      </main>
    </>
  );
}
