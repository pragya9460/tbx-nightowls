export interface HowCalculated {
  date_range: string;
  operation: string;
  records_matched: number;
  filters: Record<string, string>;
}

export interface Evidence {
  how_calculated: HowCalculated;
  source: string;
  grounded: boolean;
  breakdown?: Array<Record<string, unknown>>;
  records?: Array<Record<string, unknown>>;
  comparison?: {
    how_calculated: HowCalculated;
    breakdown?: Array<Record<string, unknown>>;
  };
}

export interface ChatResponse {
  conversation_id: string;
  answer: string;
  evidence: Evidence | null;
  query: Record<string, unknown> | null;
  refusal: {
    reason: string;
    message: string;
    suggestions: string[];
    supported?: Record<string, string[]>;
  } | null;
  meta: {
    provider?: string;
    model?: string | null;
    understanding_latency_ms?: number | null;
    token_usage?: { input_tokens?: number | null; output_tokens?: number | null } | null;
    grounded: boolean;
  };
  status?: "supported" | "empty_data" | "ambiguous" | "unsupported" | "invalid";
  confidence?: "high" | "limited" | "no_matches" | "none";
  confidence_basis?: string | null;
}

export interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  evidence?: Evidence | null;
  query?: Record<string, unknown> | null;
  refusal?: ChatResponse["refusal"];
  meta?: ChatResponse["meta"];
  isError?: boolean;
  suggestions?: string[];
  confidence?: "high" | "limited" | "no_matches" | "none";
  confidence_basis?: string | null;
}
