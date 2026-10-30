import json
import re
import uuid
from typing import List, Dict, Any, Tuple
from app.config import RULES_FILE

DEFAULT_RULES = [
    {
        "id": "rule-prophet-ligature",
        "label": "ﷺ (Arabic Ligature) -> Peace be upon him",
        "type": "regex",
        "pattern": r"[\ufdfaﷺ]",
        "replacement": "peace be upon him",
        "match_whole_word": False,
        "case_sensitive": False,
        "enabled": True
    },
    {
        "id": "rule-pbuh",
        "label": "PBUH -> Peace be upon him",
        "type": "dictionary",
        "pattern": "PBUH",
        "replacement": "peace be upon him",
        "match_whole_word": True,
        "case_sensitive": False,
        "enabled": True
    },
    {
        "id": "rule-saw",
        "label": "SAW / S.A.W. -> Peace be upon him",
        "type": "regex",
        "pattern": r"\bS\.?A\.?W\.?\b",
        "replacement": "peace be upon him",
        "match_whole_word": True,
        "case_sensitive": True,
        "enabled": True
    },
    {
        "id": "rule-swt",
        "label": "SWT / S.W.T. -> Subhanahu wa ta'ala",
        "type": "regex",
        "pattern": r"\bS\.?W\.?T\.?\b",
        "replacement": "subhanahu wa ta'ala",
        "match_whole_word": True,
        "case_sensitive": False,
        "enabled": True
    },
    {
        "id": "rule-awj",
        "label": "AWJ / A.W.J. -> Azza wa jall",
        "type": "regex",
        "pattern": r"\bA\.?W\.?J\.?\b",
        "replacement": "azza wa jall",
        "match_whole_word": True,
        "case_sensitive": False,
        "enabled": True
    },
    {
        "id": "rule-ra",
        "label": "RA / R.A. -> Radiyallahu anhu",
        "type": "regex",
        "pattern": r"\bR\.?A\.?\b",
        "replacement": "radiyallahu anhu",
        "match_whole_word": True,
        "case_sensitive": True,
        "enabled": True
    },
    {
        "id": "rule-as",
        "label": "AS / A.S. -> Alayhis salam",
        "type": "regex",
        "pattern": r"\bA\.?S\.?\b",
        "replacement": "alayhis salam",
        "match_whole_word": True,
        "case_sensitive": True,
        "enabled": True
    },
    {
        "id": "rule-citations",
        "label": "Remove citation numbers [ 1 ], [1, 2], [1-3]",
        "type": "regex",
        "pattern": r"\[\s*\d+(?:\s*[-–,]\s*\d+)*\s*\]",
        "replacement": "",
        "match_whole_word": False,
        "case_sensitive": False,
        "enabled": True
    },
    {
        "id": "rule-superscript-citations",
        "label": "Remove isolated parenthetical numbers (e.g. (1), (2))",
        "type": "regex",
        "pattern": r"\(\s*\d+\s*\)",
        "replacement": "",
        "match_whole_word": False,
        "case_sensitive": False,
        "enabled": False
    },
    {
        "id": "rule-urls",
        "label": "Simplify URLs to 'web link'",
        "type": "regex",
        "pattern": r"https?://(?:www\.)?[-a-zA-Z0-9@:%._+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b[-a-zA-Z0-9()@:%_+.~#?&/=]*",
        "replacement": "web link",
        "match_whole_word": False,
        "case_sensitive": False,
        "enabled": True
    },
    {
        "id": "rule-eg",
        "label": "e.g. -> for example",
        "type": "regex",
        "pattern": r"\b(?:e\.g\.|eg\b)",
        "replacement": "for example",
        "match_whole_word": True,
        "case_sensitive": False,
        "enabled": True
    },
    {
        "id": "rule-ie",
        "label": "i.e. -> that is",
        "type": "regex",
        "pattern": r"\b(?:i\.e\.|ie\b)",
        "replacement": "that is",
        "match_whole_word": True,
        "case_sensitive": False,
        "enabled": True
    },
    {
        "id": "rule-etc",
        "label": "etc. -> et cetera",
        "type": "regex",
        "pattern": r"\betc\.(?=\s|[!?,.]|$)",
        "replacement": "et cetera",
        "match_whole_word": True,
        "case_sensitive": False,
        "enabled": True
    },
    {
        "id": "rule-asap",
        "label": "ASAP -> as soon as possible",
        "type": "dictionary",
        "pattern": "ASAP",
        "replacement": "as soon as possible",
        "match_whole_word": True,
        "case_sensitive": False,
        "enabled": True
    },
    {
        "id": "rule-vs",
        "label": "vs. -> versus",
        "type": "regex",
        "pattern": r"\b(?:vs\.?|versus)\b",
        "replacement": "versus",
        "match_whole_word": True,
        "case_sensitive": False,
        "enabled": True
    },
    {
        "id": "rule-drs",
        "label": "Dr. -> Doctor",
        "type": "regex",
        "pattern": r"\bDr\.(?=\s+[A-Z])",
        "replacement": "Doctor",
        "match_whole_word": True,
        "case_sensitive": True,
        "enabled": True
    },
    {
        "id": "rule-ama",
        "label": "AMA -> Azure Monitor Agent",
        "type": "dictionary",
        "pattern": "AMA",
        "replacement": "Azure Monitor Agent",
        "match_whole_word": True,
        "case_sensitive": False,
        "enabled": True
    }
]


class SpeechRuleEngine:
    def __init__(self):
        self._rules: List[Dict[str, Any]] = []
        self.load_rules()

    def load_rules(self) -> List[Dict[str, Any]]:
        if RULES_FILE.exists():
            try:
                with open(RULES_FILE, "r", encoding="utf-8") as f:
                    self._rules = json.load(f)
                # Check if new default rules need to be merged in
                existing_patterns = {r.get("pattern") for r in self._rules}
                changed = False
                for def_r in DEFAULT_RULES:
                    if def_r["pattern"] not in existing_patterns:
                        self._rules.append(def_r)
                        changed = True
                if changed:
                    self.save_rules()
            except Exception as e:
                print(f"[Rules] Error loading {RULES_FILE}: {e}. Initializing default rules.")
                self._rules = list(DEFAULT_RULES)
                self.save_rules()
        else:
            self._rules = list(DEFAULT_RULES)
            self.save_rules()
        return self._rules

    def save_rules(self) -> None:
        try:
            with open(RULES_FILE, "w", encoding="utf-8") as f:
                json.dump(self._rules, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Rules] Error saving {RULES_FILE}: {e}")

    def get_rules(self) -> List[Dict[str, Any]]:
        return self._rules

    def set_rules(self, new_rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        self._rules = new_rules
        self.save_rules()
        return self._rules

    def add_rule(self, rule: Dict[str, Any]) -> Dict[str, Any]:
        if "id" not in rule or not rule["id"]:
            rule["id"] = "rule-" + uuid.uuid4().hex[:8]
        if "enabled" not in rule:
            rule["enabled"] = True
        self._rules.append(rule)
        self.save_rules()
        return rule

    def update_rule(self, rule_id: str, updated_fields: Dict[str, Any]) -> Dict[str, Any]:
        for idx, rule in enumerate(self._rules):
            if rule["id"] == rule_id:
                self._rules[idx].update(updated_fields)
                self.save_rules()
                return self._rules[idx]
        raise ValueError(f"Rule with id {rule_id} not found")

    def delete_rule(self, rule_id: str) -> bool:
        initial_len = len(self._rules)
        self._rules = [r for r in self._rules if r["id"] != rule_id]
        if len(self._rules) != initial_len:
            self.save_rules()
            return True
        return False

    def reset_to_defaults(self) -> List[Dict[str, Any]]:
        self._rules = list(DEFAULT_RULES)
        self.save_rules()
        return self._rules

    def apply_rules(self, text: str) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Applies all enabled rules sequentially to the input text.
        Returns:
            (processed_text, list_of_applied_rules_info)
        """
        current_text = text
        applied_info = []

        for rule in self._rules:
            if not rule.get("enabled", True):
                continue

            pattern = rule.get("pattern", "")
            replacement = rule.get("replacement", "")
            rule_type = rule.get("type", "dictionary")
            case_sensitive = rule.get("case_sensitive", False)
            match_whole_word = rule.get("match_whole_word", False)

            if not pattern:
                continue

            flags = 0 if case_sensitive else re.IGNORECASE

            try:
                if rule_type == "dictionary":
                    escaped_pat = re.escape(pattern)
                    if match_whole_word:
                        regex_str = rf"\b{escaped_pat}\b"
                    else:
                        regex_str = escaped_pat
                else:  # regex type
                    regex_str = pattern

                compiled = re.compile(regex_str, flags)
                if compiled.search(current_text):
                    new_text = compiled.sub(replacement, current_text)
                    if new_text != current_text:
                        applied_info.append({
                            "rule_id": rule["id"],
                            "label": rule.get("label", pattern),
                            "pattern": pattern,
                            "replacement": replacement
                        })
                        current_text = new_text

            except re.error as e:
                print(f"[Rules] Invalid regex in rule '{rule.get('label')}': {e}")
                continue

        # Clean up any leftover double spaces caused by deletion replacements
        current_text = re.sub(r"\s+", " ", current_text).strip()
        return current_text, applied_info

    def test(self, sample_text: str) -> Dict[str, Any]:
        """Test active rules on a sample text."""
        result_text, applied = self.apply_rules(sample_text)
        return {
            "original": sample_text,
            "transformed": result_text,
            "applied_rules": applied,
            "changed": sample_text != result_text
        }


# Global singleton instance
rule_engine = SpeechRuleEngine()
