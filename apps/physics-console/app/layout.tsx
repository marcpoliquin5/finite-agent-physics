import type { Metadata } from "next";
import { headers } from "next/headers";
import "./globals.css";

const title = "FINITE | Agent Physics Control Plane";
const description =
  "A constraint-native control plane for agent workflows under finite deadlines, budgets, context, reliability, and side-effect limits.";

function requestOrigin(requestHeaders: Headers): URL {
  const forwardedHost = requestHeaders.get("x-forwarded-host")?.split(",")[0]?.trim();
  const host = forwardedHost || requestHeaders.get("host") || "localhost:3001";
  const forwardedProtocol = requestHeaders.get("x-forwarded-proto")?.split(",")[0]?.trim();
  const protocol =
    forwardedProtocol === "http" || forwardedProtocol === "https"
      ? forwardedProtocol
      : host.startsWith("localhost")
        ? "http"
        : "https";

  try {
    return new URL(`${protocol}://${host}`);
  } catch {
    return new URL("http://localhost:3001");
  }
}

export async function generateMetadata(): Promise<Metadata> {
  const origin = requestOrigin(await headers());
  const socialCard = new URL("/og-v5.png", origin).toString();

  return {
    metadataBase: origin,
    title,
    description,
    icons: { icon: "/favicon.svg" },
    openGraph: {
      type: "website",
      title,
      description,
      images: [
        {
          url: socialCard,
          width: 1536,
          height: 1024,
          alt: "FINITE Agent Physics v5 constraint-native control plane",
        },
      ],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: [socialCard],
    },
  };
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
