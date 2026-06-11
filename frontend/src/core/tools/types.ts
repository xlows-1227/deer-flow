export interface ImageGenerationProviderMetadata {
  id: string;
  display_name: string;
  default_base_url: string;
  default_model: string;
  models: string[];
  supported_parameters: string[];
  required_parameters: string[];
  size_options: string[];
  quality_options: string[];
  style_options: string[];
  moderation_options: string[];
  background_options: string[];
  max_images: number;
  api_key_label: string;
  docs_url: string;
}

export interface ImageGenerationProviderConfig {
  enabled: boolean;
  provider: string;
  display_name: string;
  api_key?: string | null;
  has_api_key: boolean;
  base_url: string;
  model: string;
  timeout_seconds: number;
  trust_env: boolean;
  params: Record<string, unknown>;
  metadata?: ImageGenerationProviderMetadata | null;
}

export interface ImageGenerationConfig {
  enabled: boolean;
  default_provider?: string | null;
  output_subdir: string;
  providers: Record<string, ImageGenerationProviderConfig>;
  provider_metadata: Record<string, ImageGenerationProviderMetadata>;
}

export interface ImageGenerationProviderConfigUpdate {
  enabled: boolean;
  provider?: string | null;
  display_name?: string | null;
  api_key?: string | null;
  base_url?: string | null;
  model?: string | null;
  timeout_seconds: number;
  trust_env: boolean;
  params: Record<string, unknown>;
}

export interface ImageGenerationConfigUpdate {
  enabled: boolean;
  default_provider?: string | null;
  output_subdir: string;
  providers: Record<string, ImageGenerationProviderConfigUpdate>;
}
