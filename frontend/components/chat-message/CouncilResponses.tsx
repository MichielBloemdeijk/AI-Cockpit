"use client";

import { ChevronDown, ChevronRight } from "lucide-react";

import type { CouncilResponse } from "../../lib/api";
import { formatTokenCount, formatUsageCost } from "../../lib/usage";

interface Props {
  councilData: CouncilResponse;
  councilExpanded: boolean;
  onToggle: () => void;
}

export function CouncilResponses({ councilData, councilExpanded, onToggle }: Props) {
  const councilTotalCost = formatUsageCost(councilData.total_usage?.cost);

  return (
    <div className="w-full">
      <button
        onClick={onToggle}
        className="flex items-center gap-1 text-xs text-zinc-500 transition-colors hover:text-zinc-300"
      >
        {councilExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {councilData.model_responses.length} model responses{councilTotalCost ? ` · ${councilTotalCost} credits total` : ""}
      </button>

      {councilExpanded && (
        <div className="mt-2 space-y-2 border-l border-zinc-800/80 pl-3">
          {councilData.model_responses.map((response) => (
            <div key={response.model}>
              <div className="text-xs font-mono text-zinc-500">{response.model}</div>
              {(response.usage?.cost || response.usage?.total_tokens) && (
                <div className="mt-1 flex flex-wrap gap-3 text-[11px] text-zinc-500">
                  {typeof response.usage?.cost === "number" && <span>Cost {formatUsageCost(response.usage.cost)} credits</span>}
                  {typeof response.usage?.total_tokens === "number" && <span>{formatTokenCount(response.usage.total_tokens)} tokens</span>}
                </div>
              )}
              {response.error ? (
                <div className="mt-1 text-xs text-red-400">{response.error}</div>
              ) : (
                <div className="mt-1 whitespace-pre-wrap text-xs text-zinc-300">{response.content}</div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}