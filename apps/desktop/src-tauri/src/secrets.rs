//! OS keychain integration for Magnm Studio API keys.

use serde::Serialize;

/// Service name used when storing credentials in the OS keychain.
pub const SERVICE_NAME: &str = "magnm-studio";

/// Allow-list of environment variable names managed in the OS keychain.
pub const KNOWN_SECRET_KEYS: &[&str] = &[
    "NVIDIA_NIM_API_KEY",
    "NVIDIA_NIM_API_KEY1",
    "NVIDIA_NIM_API_KEY2",
    "NVIDIA_NIM_API_KEY3",
    "NVIDIA_NIM_API_KEY4",
    "NVIDIA_NIM_API_KEY5",
    "NVIDIA_NIM_API_KEY6",
    "NVIDIA_NIM_API_KEY7",
    "NVIDIA_NIM_API_KEY8",
    "NVIDIA_NIM_API_KEY9",
    "NVIDIA_NIM_API_KEY10",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "NVIDIA_NIM_API_KEY_CMO",
    "NVIDIA_NIM_API_KEY_STRATEGIST",
    "NVIDIA_NIM_API_KEY_ADVISOR",
    "NVIDIA_NIM_API_KEY_RESEARCHER",
    "NVIDIA_NIM_API_KEY_BLOG_WRITER",
    "NVIDIA_NIM_API_KEY_WRITER",
    "NVIDIA_NIM_API_KEY_DESIGNER",
    "NVIDIA_NIM_API_KEY_VIDEO_PRODUCER",
    "NVIDIA_NIM_API_KEY_REVIEWER",
    "NVIDIA_NIM_API_KEY_ANALYST",
    "NVIDIA_NIM_API_KEY_LISTENER",
    "NVIDIA_NIM_API_KEY_COMMUNITY",
    "NVIDIA_NIM_API_KEY_GROWTH",
];

/// Public status of a secret key (never contains the secret value).
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct SecretStatus {
    pub name: String,
    pub is_set: bool,
}

/// Return true if `name` is in the allow-list of managed API keys.
pub fn is_allowed_secret_name(name: &str) -> bool {
    KNOWN_SECRET_KEYS.contains(&name)
}

fn validate_secret_name(name: &str) -> Result<(), String> {
    if is_allowed_secret_name(name) {
        Ok(())
    } else {
        Err(format!("Unknown secret key: {name}"))
    }
}

/// Return the set/unset status for every known secret key.
///
/// If the OS keychain is unavailable (for example, no secret service on Linux),
/// all keys are reported with `is_set = false` so the application still starts.
pub fn list_secrets_status() -> Vec<SecretStatus> {
    KNOWN_SECRET_KEYS
        .iter()
        .map(|&name| {
            let is_set = match keyring::Entry::new(SERVICE_NAME, name) {
                Ok(entry) => match entry.get_password() {
                    Ok(value) => !value.trim().is_empty(),
                    Err(_) => false,
                },
                Err(_) => false,
            };
            SecretStatus {
                name: name.to_string(),
                is_set,
            }
        })
        .collect()
}

/// Load all configured secrets from the OS keychain to pass to the engine env.
///
/// Never fails if the keychain service is unavailable; logs a warning and
/// returns any keys that could be read.
pub fn load_secrets_for_env() -> Vec<(String, String)> {
    let mut pairs = Vec::new();
    let mut warned_unavailable = false;

    for &name in KNOWN_SECRET_KEYS {
        let entry = match keyring::Entry::new(SERVICE_NAME, name) {
            Ok(entry) => entry,
            Err(err) => {
                if !warned_unavailable {
                    eprintln!("[magnm-studio] Keychain unavailable: {err}");
                    warned_unavailable = true;
                }
                continue;
            }
        };

        match entry.get_password() {
            Ok(value) if !value.trim().is_empty() => {
                pairs.push((name.to_string(), value));
            }
            Ok(_) | Err(keyring::Error::NoEntry) => {}
            Err(err) => {
                if !warned_unavailable {
                    eprintln!("[magnm-studio] Could not read keychain ({err}); continuing without keychain secrets");
                    warned_unavailable = true;
                }
            }
        }
    }

    pairs
}

/// Store or update a secret value in the OS keychain.
pub fn set_secret_value(name: &str, value: &str) -> Result<(), String> {
    validate_secret_name(name)?;
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err("Secret value cannot be empty".to_string());
    }

    let entry = keyring::Entry::new(SERVICE_NAME, name)
        .map_err(|err| format!("OS keychain is unavailable: {err}"))?;
    entry
        .set_password(trimmed)
        .map_err(|err| format!("Failed to store secret '{name}' in OS keychain: {err}"))
}

/// Delete a secret from the OS keychain (idempotent if already absent).
pub fn delete_secret_value(name: &str) -> Result<(), String> {
    validate_secret_name(name)?;

    let entry = keyring::Entry::new(SERVICE_NAME, name)
        .map_err(|err| format!("OS keychain is unavailable: {err}"))?;
    match entry.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(err) => Err(format!(
            "Failed to delete secret '{name}' from OS keychain: {err}"
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn allow_list_accepts_known_keys_and_rejects_others() {
        assert!(is_allowed_secret_name("NVIDIA_NIM_API_KEY"));
        for idx in 1..=10 {
            let key_name = format!("NVIDIA_NIM_API_KEY{idx}");
            assert!(is_allowed_secret_name(&key_name));
        }
        assert!(is_allowed_secret_name("OPENAI_API_KEY"));
        assert!(is_allowed_secret_name("ANTHROPIC_API_KEY"));
        assert!(is_allowed_secret_name("GEMINI_API_KEY"));
        assert!(is_allowed_secret_name("GROQ_API_KEY"));
        assert!(is_allowed_secret_name("NVIDIA_NIM_API_KEY_CMO"));
        assert!(is_allowed_secret_name("NVIDIA_NIM_API_KEY_VIDEO_PRODUCER"));
        assert!(is_allowed_secret_name("NVIDIA_NIM_API_KEY_GROWTH"));

        assert!(!is_allowed_secret_name(""));
        assert!(!is_allowed_secret_name("PATH"));
        assert!(!is_allowed_secret_name("MAGNM_ENGINE_TOKEN"));
        assert!(!is_allowed_secret_name("nvidia_nim_api_key"));
        assert!(!is_allowed_secret_name("NVIDIA_NIM_API_KEY0"));
        assert!(!is_allowed_secret_name("NVIDIA_NIM_API_KEY11"));
        assert!(!is_allowed_secret_name("NVIDIA_NIM_API_KEY_UNKNOWN"));
    }

    #[test]
    fn set_and_delete_reject_unknown_keys_before_touching_keychain() {
        let set_err = set_secret_value("UNSAFE_ENV_VAR", "secret").unwrap_err();
        assert!(set_err.contains("Unknown secret key"));

        let del_err = delete_secret_value("UNSAFE_ENV_VAR").unwrap_err();
        assert!(del_err.contains("Unknown secret key"));
    }
}
