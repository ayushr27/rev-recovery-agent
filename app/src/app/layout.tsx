import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Revenue Recovery Agent",
  description: "Batch audit trail and recovery metrics for failed Razorpay payments.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
