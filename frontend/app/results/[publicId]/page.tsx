"use client";

import { use } from "react";
import ResultScreen from "../ResultScreen";

export default function ResultPage({ params }: { params: Promise<{ publicId: string }> }) {
  return <ResultScreen publicId={use(params).publicId} />;
}
