import forge from "node-forge";

const ENCRYPTED_PASSWORD_PREFIX = "rsa-oaep-sha256:";

type LoginPublicKeyResponse = {
  enabled: boolean;
  required: boolean;
  algorithm?: string | null;
  public_key?: string | null;
};

/**
 * Encrypt a login password when the gateway has application-layer encryption
 * enabled. node-forge is intentionally used instead of Web Crypto because this
 * temporary path must also work on non-localhost HTTP origins.
 */
export async function encryptLoginPassword(password: string): Promise<string> {
  const response = await fetch("/api/v1/auth/login/public-key", {
    cache: "no-store",
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw new Error("Unable to load the login encryption key");
  }

  const config = (await response.json()) as LoginPublicKeyResponse;
  if (!config.enabled || !config.public_key) {
    if (config.required) {
      throw new Error("Login encryption is required but not configured");
    }
    return password;
  }

  try {
    const publicKey = forge.pki.publicKeyFromPem(config.public_key);
    const encrypted = publicKey.encrypt(password, "RSA-OAEP", {
      md: forge.md.sha256.create(),
      mgf1: { md: forge.md.sha256.create() },
    });
    return `${ENCRYPTED_PASSWORD_PREFIX}${forge.util.encode64(encrypted)}`;
  } catch {
    throw new Error("Unable to encrypt the login password");
  }
}
