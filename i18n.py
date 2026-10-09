"""Internationalization (i18n) module for Dedikka backend."""
from typing import Literal

Lang = Literal["fr", "en"]

TRANSLATIONS: dict[str, dict[Lang, str]] = {
    "csrf_invalid": {
        "fr": "Jeton CSRF absent ou invalide.",
        "en": "Missing or invalid CSRF token.",
    },
    "password_mismatch": {
        "fr": "Les mots de passe ne correspondent pas.",
        "en": "Passwords do not match.",
    },
    "email_in_use": {
        "fr": "Cette adresse e-mail est déjà utilisée.",
        "en": "This email address is already in use.",
    },
    "invalid_credentials": {
        "fr": "E-mail ou mot de passe incorrect.",
        "en": "Incorrect email or password.",
    },
    "auth_required": {
        "fr": "Connexion requise.",
        "en": "Authentication required.",
    },
    "rate_limited": {
        "fr": "Trop de tentatives. Veuillez patienter un instant.",
        "en": "Too many attempts. Please try again shortly.",
    },
    "image_format_unsupported": {
        "fr": "Formats autorisés : JPEG, PNG, WebP et GIF.",
        "en": "Allowed formats: JPEG, PNG, WebP, and GIF.",
    },
    "image_too_large": {
        "fr": "Image limitée à 8 Mo.",
        "en": "Image is limited to 8 MB.",
    },
    "image_invalid_content": {
        "fr": "Le contenu du fichier ne correspond pas à une image valide.",
        "en": "File content does not match a valid image.",
    },
    "image_webp_invalid": {
        "fr": "Fichier WebP invalide.",
        "en": "Invalid WebP image file.",
    },
    "storage_upload_failed": {
        "fr": "Échec de l’envoi de l’image vers l’espace de stockage.",
        "en": "Failed to upload image to storage.",
    },
    "storage_unreachable": {
        "fr": "L’espace de stockage est actuellement inaccessible.",
        "en": "Storage is currently unreachable.",
    },
    "page_data_too_large": {
        "fr": "Les données de la page sont trop volumineuses.",
        "en": "Page content is too large.",
    },
    "image_ownership_invalid": {
        "fr": "Une image ne correspond pas à votre espace de stockage.",
        "en": "An image does not belong to your storage area.",
    },
    "slug_in_use": {
        "fr": "Ce lien personnalisé est déjà utilisé.",
        "en": "This custom link is already in use.",
    },
    "page_not_found": {
        "fr": "Cette dédicace n’existe pas.",
        "en": "This dedication does not exist.",
    },
    "page_forbidden": {
        "fr": "Vous n’êtes pas autorisé à modifier cette dédicace.",
        "en": "You are not authorized to modify this dedication.",
    },
    "page_deleted": {
        "fr": "Dédicace supprimée avec succès.",
        "en": "Dedication deleted successfully.",
    },
    "profile_updated": {
        "fr": "Profil mis à jour avec succès.",
        "en": "Profile updated successfully.",
    },
}


def get_lang_from_header(accept_language: str | None) -> Lang:
    """Detects preferred language ('fr' or 'en') from Accept-Language header."""
    if not accept_language:
        return "fr"
    al = accept_language.lower()
    # Simple check for English preference
    if "en" in al and (al.find("en") < al.find("fr") if "fr" in al else True):
        return "en"
    return "fr"


def translate(key: str, lang: Lang = "fr") -> str:
    """Returns the translation for the given key and language code."""
    entry = TRANSLATIONS.get(key)
    if not entry:
        return key
    return entry.get(lang, entry.get("fr", key))
