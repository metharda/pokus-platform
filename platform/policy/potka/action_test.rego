# The cases of the decision: every rule by its ID, the order between rules, what a
# missing or mistyped fact does, the edges of the budgets and thresholds, whose word
# a tool's facts are, and the bundle's hash.
package potka.action_test

import data.potka.action

# An agent's low-risk action after a diagnosis it fits, within every budget: allowed.
base := {
	"caller": "agent",
	"mode": "autonomous_low_risk",
	"tool": tool_of("start_service"),
	"call": {"valid": true, "error": null},
	"args": {"node": "web", "unit": "nginx.service"},
	"meta": {"action_confidence": 0.9},
	"diagnosis": {"present": true, "label": "service.stopped", "confidence": 0.9},
	"counters": {"steps": 3, "mutating_calls": 0, "same_action_repeats": 0},
	"budgets": {"max_mutating_calls": 5, "max_same_action_repeats": 2},
	"thresholds": {"min_action_confidence": 0.6, "min_diagnosis_confidence": 0.5},
}

# A tool of the registry as a valid call names it.
tool_of(name) := {"name": name, "known": true, "offered": true, "version": data.tools[name].version}

# A diagnosis that fits the tool NAME.
fitting(name) := {"present": true, "label": data.tools[name].applicable_categories[0], "confidence": 0.9}

# A call whose arguments were not checked or did not fit: only the tool's name, and the code.
unchecked(name, known, offered, code) := {
	"tool": {"name": name, "known": known, "offered": offered},
	"call": {"valid": false, "error": code},
	"args": {},
	"meta": {},
}

decide(asked) := found if {
	found := action.decision with input as asked
}

# The base with these keys replaced whole.
replaced(keys) := object.union(object.remove(base, object.keys(keys)), keys)

# The base with these values laid over it, key by key.
patched(patch) := object.union(base, patch)

# The base without what these JSON pointers name.
lacking(pointers) := json.remove(base, pointers)

rule(asked) := decide(asked).rule_id

whole(effect, rule_id, reason) := {
	"effect": effect,
	"rule_id": rule_id,
	"reason": reason,
	"bundle_hash": data.potka.bundle.hash,
}

mutating_tools contains name if {
	some name, entry in data.tools
	entry.mutating == true
	entry.risk != "forbidden"
}

read_only_tools contains name if {
	some name, entry in data.tools
	entry.mutating == false
}

modes := {"diagnose_only", "recommend", "autonomous_low_risk", "human_in_loop"}

# --- Every rule, by its ID -------------------------------------------------------------

test_rule_tool_unknown if {
	asked := replaced(unchecked("no_such_tool", false, false, "unknown_tool"))
	decide(asked) == whole("deny", "tool_unknown", "tool_unknown")
}

test_rule_tool_forbidden if {
	asked := replaced(unchecked("flush_firewall", true, false, "forbidden"))
	decide(asked) == whole("deny", "tool_forbidden", "tool_forbidden")
}

test_rule_tool_not_offered if {
	asked := replaced(unchecked("docker_ps", true, false, "not_offered"))
	decide(asked) == whole("deny", "tool_not_offered", "tool_not_offered")
}

test_rule_call_invalid if {
	asked := replaced(unchecked("start_service", true, true, "path_not_allowed"))
	decide(asked) == whole("deny", "call_invalid", "invalid_call:path_not_allowed")
}

test_rule_caller_unknown if {
	decide(patched({"caller": "runner"})) == whole("deny", "caller_unknown", "caller_unknown")
}

test_rule_evidence_profile if {
	asked := replaced({"caller": "orchestrator", "tool": tool_of("read_file"), "meta": {}})
	decide(asked) == whole("allow", "evidence_profile", "read_only_for_orchestrator")
}

test_rule_orchestrator_never_mutates if {
	asked := patched({"caller": "orchestrator"})
	decide(asked) == whole("deny", "orchestrator_never_mutates", "orchestrator_never_mutates")
}

test_rule_read_only if {
	asked := replaced({"tool": tool_of("read_file"), "meta": {}})
	decide(asked) == whole("allow", "read_only", "read_only")
}

test_rule_diagnose_only if {
	decide(patched({"mode": "diagnose_only"})) == whole("deny", "diagnose_only", "diagnose_only_mode")
}

test_rule_recommend if {
	decide(patched({"mode": "recommend"})) == whole("deny", "recommend", "recommend_mode_not_executed")
}

test_rule_mode_unknown if {
	decide(patched({"mode": "yolo"})) == whole("deny", "mode_unknown", "mode_unknown")
}

test_rule_diagnosis_required if {
	asked := replaced({"diagnosis": {"present": false, "label": null, "confidence": null}})
	decide(asked) == whole("deny", "diagnosis_required", "diagnosis_required_before_action")
}

test_rule_diagnosis_inconsistent if {
	not "network.dns_failure" in data.tools.start_service.applicable_categories
	asked := patched({"diagnosis": {"label": "network.dns_failure"}})
	decide(asked) == whole("deny", "diagnosis_inconsistent", "action_inconsistent_with_diagnosis")
}

test_rule_repeat_budget if {
	asked := patched({"counters": {"same_action_repeats": 3}})
	decide(asked) == whole("deny", "repeat_budget", "same_action_repeated_too_often")
}

test_rule_mutating_budget if {
	asked := patched({"counters": {"mutating_calls": 5}})
	decide(asked) == whole("deny", "mutating_budget", "mutating_budget_exceeded")
}

test_rule_high_risk if {
	data.tools.kill_process.risk == "high"
	asked := replaced({"tool": tool_of("kill_process"), "diagnosis": fitting("kill_process")})
	decide(asked) == whole("require_approval", "high_risk", "high_risk")
}

test_rule_medium_risk if {
	data.tools.restart_service.risk == "medium"
	asked := replaced({"tool": tool_of("restart_service")})
	decide(asked) == whole("require_approval", "medium_risk", "medium_risk")
}

test_rule_low_risk_confident if {
	data.tools.start_service.risk == "low"
	decide(base) == whole("allow", "low_risk_confident", "low_risk_confident")
}

test_rule_low_risk_unsure if {
	asked := patched({"mode": "human_in_loop", "meta": {"action_confidence": 0.1}})
	decide(asked) == whole("require_approval", "low_risk_unsure", "low_risk_below_thresholds")
}

test_rule_low_risk_unsure_autonomous if {
	asked := patched({"meta": {"action_confidence": 0.1}})
	decide(asked) == whole("deny", "low_risk_unsure_autonomous", "low_risk_below_thresholds")
}

test_rule_input_not_understood if {
	# An input that leaves a fact out.
	decide(lacking(["/counters"])) == whole("deny", "input_not_understood", "input_not_understood")

	# And what no rule holds for: a mutating tool with a risk no rule knows, which only
	# another registry could hold.
	odd := {"odd": {"version": 1, "risk": "odd", "mutating": true, "applicable_categories": ["service.stopped"]}}
	asked := replaced({"tool": {"name": "odd", "known": true, "offered": true, "version": 1}})
	found := action.decision with input as asked with data.tools as odd
	found == whole("deny", "input_not_understood", "input_not_understood")
}

# --- The order: of two rules that both hold, the earlier decides -------------------------

test_order_unknown_before_forbidden if {
	rule(replaced(unchecked("flush_firewall", false, false, "unknown_tool"))) == "tool_unknown"
}

test_order_unknown_before_everything_of_the_orchestrator if {
	asked := replaced(unchecked("no_such_tool", false, true, "unknown_tool"))
	rule(object.union(asked, {"caller": "orchestrator"})) == "tool_unknown"
}

test_order_forbidden_before_not_offered if {
	rule(replaced(unchecked("flush_firewall", true, false, "not_offered"))) == "tool_forbidden"
}

test_order_forbidden_before_an_unknown_caller if {
	asked := replaced(unchecked("flush_firewall", true, true, "forbidden"))
	rule(object.union(asked, {"caller": "runner"})) == "tool_forbidden"
}

test_order_not_offered_before_invalid if {
	rule(replaced(unchecked("docker_ps", true, false, "path_not_allowed"))) == "tool_not_offered"
}

test_order_not_offered_before_the_evidence_profile if {
	asked := replaced({"caller": "orchestrator", "tool": object.union(tool_of("read_file"), {"offered": false})})
	rule(asked) == "tool_not_offered"
}

test_order_invalid_before_an_unknown_caller if {
	asked := replaced(unchecked("start_service", true, true, "bad_form"))
	rule(object.union(asked, {"caller": "runner"})) == "call_invalid"
}

test_order_invalid_before_the_evidence_profile if {
	asked := replaced(unchecked("read_file", true, true, "path_not_allowed"))
	rule(object.union(asked, {"caller": "orchestrator"})) == "call_invalid"
}

test_order_an_unknown_caller_before_read_only if {
	rule(replaced({"caller": "runner", "tool": tool_of("read_file")})) == "caller_unknown"
}

test_order_the_orchestrator_before_the_modes if {
	every mode in modes {
		rule(patched({"caller": "orchestrator", "mode": mode})) == "orchestrator_never_mutates"
	}
}

test_order_read_only_before_the_modes_and_the_diagnosis if {
	every mode in modes | {"yolo"} {
		asked := replaced({
			"tool": tool_of("read_file"),
			"mode": mode,
			"diagnosis": {"present": false, "label": null, "confidence": null},
		})
		rule(asked) == "read_only"
	}
}

test_order_recommend_before_a_missing_diagnosis if {
	asked := replaced({"mode": "recommend", "diagnosis": {"present": false, "label": null, "confidence": null}})
	rule(asked) == "recommend"
}

test_order_an_unknown_mode_before_a_missing_diagnosis if {
	asked := replaced({"mode": "yolo", "diagnosis": {"present": false, "label": null, "confidence": null}})
	rule(asked) == "mode_unknown"
}

test_order_an_unknown_mode_before_every_approval if {
	asked := replaced({"mode": "yolo", "tool": tool_of("kill_process"), "diagnosis": fitting("kill_process")})
	rule(asked) == "mode_unknown"
}

test_order_a_missing_diagnosis_before_an_inconsistent_one if {
	asked := replaced({"diagnosis": {"present": false, "label": "network.dns_failure", "confidence": 0.9}})
	rule(asked) == "diagnosis_required"
}

test_order_the_diagnosis_before_the_budgets if {
	asked := patched({"diagnosis": {"label": "network.dns_failure"}, "counters": {"same_action_repeats": 3}})
	rule(asked) == "diagnosis_inconsistent"
}

test_order_the_repeat_budget_before_the_mutating_budget if {
	asked := patched({"counters": {"same_action_repeats": 3, "mutating_calls": 5}})
	rule(asked) == "repeat_budget"
}

test_order_the_budgets_before_every_risk if {
	every name in {"kill_process", "restart_service", "start_service"} {
		asked := replaced({
			"tool": tool_of(name),
			"diagnosis": fitting(name),
			"counters": {"steps": 9, "mutating_calls": 5, "same_action_repeats": 0},
		})
		rule(asked) == "mutating_budget"
	}
}

test_order_a_missing_diagnosis_before_every_risk if {
	every name in {"kill_process", "restart_service", "start_service"} {
		asked := replaced({"tool": tool_of(name), "diagnosis": {"present": false, "label": null, "confidence": null}})
		rule(asked) == "diagnosis_required"
	}
}

# --- A missing fact, or one of another type, denies ------------------------------------

missing := {
	"/tool",
	"/tool/name",
	"/tool/known",
	"/tool/offered",
	"/tool/version",
	"/call",
	"/call/valid",
	"/caller",
	"/mode",
	"/diagnosis",
	"/diagnosis/present",
	"/diagnosis/label",
	"/diagnosis/confidence",
	"/counters",
	"/counters/same_action_repeats",
	"/counters/mutating_calls",
	"/budgets",
	"/budgets/max_same_action_repeats",
	"/budgets/max_mutating_calls",
	"/meta",
	"/meta/action_confidence",
	"/thresholds",
	"/thresholds/min_action_confidence",
	"/thresholds/min_diagnosis_confidence",
}

# The two modes in which an agent's action can be allowed or sent to an approval.
acting := {"autonomous_low_risk", "human_in_loop"}

not_understood := whole("deny", "input_not_understood", "input_not_understood")

test_a_missing_fact_denies if {
	every pointer in missing {
		every mode in acting {
			asked := json.remove(patched({"mode": mode}), [pointer])
			decide(asked) == not_understood
		}
	}
}

mistyped := [
	{"tool": {"known": "true"}},
	{"tool": {"name": 5}},
	{"tool": {"version": "1"}},
	{"tool": {"offered": 1}},
	{"tool": {"offered": "true"}},
	{"call": {"valid": "true"}},
	{"call": {"valid": 1}},
	{"caller": null},
	{"caller": ["agent"]},
	{"mode": 1},
	{"diagnosis": {"present": "true"}},
	{"diagnosis": {"label": 7}},
	{"diagnosis": {"confidence": "0.9"}},
	{"counters": {"same_action_repeats": "0"}},
	{"counters": {"mutating_calls": null}},
	{"budgets": {"max_same_action_repeats": "9"}},
	{"budgets": {"max_mutating_calls": "9"}},
	{"meta": {"action_confidence": "0.9"}},
	{"thresholds": {"min_action_confidence": "0.1"}},
	{"thresholds": {"min_diagnosis_confidence": null}},
]

test_a_fact_of_another_type_denies if {
	every patch in mistyped {
		every mode in acting {
			decide(patched(object.union({"mode": mode}, patch))) == not_understood
		}
	}
}

# A count below zero or with a fraction, and a share outside zero to one.
out_of_bounds := [
	{"counters": {"mutating_calls": -1}},
	{"counters": {"same_action_repeats": -1}},
	{"counters": {"mutating_calls": 0.5}},
	{"budgets": {"max_mutating_calls": -1}},
	{"budgets": {"max_same_action_repeats": 1.5}},
	{"meta": {"action_confidence": 1.1}},
	{"meta": {"action_confidence": -0.1}},
	{"diagnosis": {"confidence": 1.1}},
	{"diagnosis": {"confidence": -0.1}},
	{"thresholds": {"min_action_confidence": -1}},
	{"thresholds": {"min_diagnosis_confidence": 1.5}},
]

test_a_fact_out_of_its_bounds_denies if {
	every patch in out_of_bounds {
		every mode in acting {
			decide(patched(object.union({"mode": mode}, patch))) == not_understood
		}
	}
}

test_a_negative_count_never_fits_a_budget_of_none if {
	asked := patched({"counters": {"mutating_calls": -3}, "budgets": {"max_mutating_calls": 0}})
	decide(asked) == not_understood
}

test_a_call_that_is_not_valid_needs_no_version_and_no_confidence if {
	asked := replaced(unchecked("start_service", true, true, "bad_form"))
	not asked.tool.version
	not asked.meta.action_confidence
	rule(asked) == "call_invalid"
}

test_a_read_states_no_confidence_and_an_action_must if {
	rule(replaced({"tool": tool_of("read_file"), "meta": {}})) == "read_only"
	decide(replaced({"meta": {}})) == not_understood
}

test_before_a_diagnosis_its_label_and_confidence_are_not_asked_for if {
	asked := replaced({"tool": tool_of("read_file"), "meta": {}, "diagnosis": {"present": false}})
	rule(asked) == "read_only"
	rule(replaced({"diagnosis": {"present": false}})) == "diagnosis_required"
}

test_a_string_is_greater_than_every_number_and_still_no_confidence if {
	"0.9" > 1
	rule(patched({"meta": {"action_confidence": "0.9"}})) == "input_not_understood"
}

test_no_input_at_all_is_denied if {
	decide({}) == not_understood
}

test_an_error_that_is_no_code_is_not_repeated if {
	every said in ["Bad Code!", "a b", "", 5, null, ["x"], "x:y"] {
		asked := replaced(unchecked("start_service", true, true, said))
		decide(asked) == whole("deny", "call_invalid", "invalid_call")
	}
}

# --- The edges ------------------------------------------------------------------------------

test_an_action_may_be_repeated_as_often_as_its_budget_says if {
	rule(patched({"counters": {"same_action_repeats": 2}})) == "low_risk_confident"
	rule(patched({"counters": {"same_action_repeats": 3}})) == "repeat_budget"
}

test_the_last_mutating_call_of_the_budget_is_allowed if {
	rule(patched({"counters": {"mutating_calls": 4}})) == "low_risk_confident"
	rule(patched({"counters": {"mutating_calls": 5}})) == "mutating_budget"
}

test_a_budget_of_no_mutating_call_denies_the_first if {
	rule(patched({"budgets": {"max_mutating_calls": 0}})) == "mutating_budget"
}

test_a_confidence_at_its_threshold_is_enough if {
	rule(patched({"meta": {"action_confidence": 0.6}})) == "low_risk_confident"
	rule(patched({"meta": {"action_confidence": 0.59}})) == "low_risk_unsure_autonomous"
	rule(patched({"diagnosis": {"confidence": 0.5}})) == "low_risk_confident"
	rule(patched({"diagnosis": {"confidence": 0.49}})) == "low_risk_unsure_autonomous"
}

test_both_confidences_are_needed if {
	rule(patched({"meta": {"action_confidence": 1}, "diagnosis": {"confidence": 0.1}})) == "low_risk_unsure_autonomous"
	rule(patched({"meta": {"action_confidence": 0.1}, "diagnosis": {"confidence": 1}})) == "low_risk_unsure_autonomous"
}

test_a_high_confidence_approves_no_medium_or_high_risk if {
	every name in {"kill_process", "restart_service"} {
		every mode in {"autonomous_low_risk", "human_in_loop"} {
			asked := replaced({
				"tool": tool_of(name),
				"mode": mode,
				"meta": {"action_confidence": 1},
				"diagnosis": object.union(fitting(name), {"confidence": 1}),
			})
			decide(asked).effect == "require_approval"
		}
	}
}

# --- A tool's facts are the bundle's ---------------------------------------------------------

test_the_inputs_word_on_a_tools_risk_counts_for_nothing if {
	lie := {"risk": "read", "mutating": false, "applicable_categories": []}
	asked := replaced({"tool": object.union(tool_of("restart_service"), lie)})
	rule(asked) == "medium_risk"
}

test_a_tool_the_bundle_has_not_is_unknown_whatever_the_input_says if {
	asked := replaced({"tool": {"name": "no_such_tool", "known": true, "offered": true, "version": 1}})
	rule(asked) == "tool_unknown"
}

test_a_tool_at_another_version_than_the_bundles_is_unknown if {
	asked := patched({"tool": {"version": data.tools.start_service.version + 1}})
	rule(asked) == "tool_unknown"
}

test_every_tool_of_the_registry_has_a_risk_the_rules_know if {
	every entry in data.tools {
		entry.risk in {"read", "low", "medium", "high", "forbidden"}
		is_boolean(entry.mutating)
		is_number(entry.version)
	}
}

test_a_tool_that_reads_is_read_only_and_a_risk_above_read_mutates if {
	every entry in data.tools {
		(entry.risk == "read") == entry.mutating == false
	}
}

# --- The orchestrator never mutates ------------------------------------------------------

never_mutates := whole("deny", "orchestrator_never_mutates", "orchestrator_never_mutates")

test_the_orchestrator_is_denied_every_mutating_tool_in_every_mode_offered_or_not if {
	count(mutating_tools) > 0
	every name in mutating_tools {
		every mode in modes {
			every offered in {true, false} {
				asked := replaced({
					"caller": "orchestrator",
					"mode": mode,
					"tool": object.union(tool_of(name), {"offered": offered}),
					"meta": {"action_confidence": 1},
					"diagnosis": object.union(fitting(name), {"confidence": 1}),
				})
				decide(asked) == never_mutates
			}
		}
	}
}

# The orchestrator's denial, position by position among the denials: after an unknown and
# after a forbidden tool, before one that is not offered and before a call that is not valid.

test_order_the_orchestrators_denial_before_not_offered if {
	asked := replaced(unchecked("start_service", true, false, "not_offered"))
	decide(object.union(asked, {"caller": "orchestrator"})) == never_mutates
}

test_order_the_orchestrators_denial_before_invalid if {
	asked := replaced(unchecked("start_service", true, true, "bad_form"))
	decide(object.union(asked, {"caller": "orchestrator"})) == never_mutates
}

test_order_the_orchestrators_denial_before_a_call_that_is_neither_offered_nor_valid if {
	asked := replaced(unchecked("restart_service", true, false, "missing_argument"))
	decide(object.union(asked, {"caller": "orchestrator"})) == never_mutates
}

test_the_orchestrators_denial_is_the_orchestrators_alone if {
	# The same three calls from an agent are the ordinary refusals.
	rule(replaced(unchecked("start_service", true, false, "not_offered"))) == "tool_not_offered"
	rule(replaced(unchecked("start_service", true, true, "bad_form"))) == "call_invalid"
	rule(replaced(unchecked("restart_service", true, false, "missing_argument"))) == "tool_not_offered"
}

test_the_orchestrators_read_that_is_not_offered_or_not_valid_is_an_ordinary_refusal if {
	not_offered := replaced(unchecked("read_file", true, false, "not_offered"))
	rule(object.union(not_offered, {"caller": "orchestrator"})) == "tool_not_offered"
	invalid := replaced(unchecked("read_file", true, true, "path_not_allowed"))
	rule(object.union(invalid, {"caller": "orchestrator"})) == "call_invalid"
}

test_the_orchestrator_is_allowed_every_read_only_tool_it_is_offered if {
	count(read_only_tools) > 0
	every name in read_only_tools {
		every mode in modes {
			asked := replaced({"caller": "orchestrator", "mode": mode, "tool": tool_of(name), "meta": {}})
			decide(asked) == whole("allow", "evidence_profile", "read_only_for_orchestrator")
		}
	}
}

test_order_forbidden_before_the_orchestrators_denial if {
	every offered in {true, false} {
		asked := replaced(unchecked("flush_firewall", true, offered, "forbidden"))
		found := decide(object.union(asked, {"caller": "orchestrator"}))
		found == whole("deny", "tool_forbidden", "tool_forbidden")
	}
}

# --- The agent ---------------------------------------------------------------------------------

test_no_mode_but_two_lets_an_agent_mutate if {
	every name in mutating_tools {
		every mode in {"diagnose_only", "recommend", "yolo"} {
			asked := replaced({"mode": mode, "tool": tool_of(name), "diagnosis": fitting(name)})
			decide(asked).effect == "deny"
		}
	}
}

test_an_agent_reads_in_every_mode if {
	every name in read_only_tools {
		every mode in modes {
			asked := replaced({"mode": mode, "tool": tool_of(name), "meta": {}})
			decide(asked) == whole("allow", "read_only", "read_only")
		}
	}
}

# --- The bundle's hash ----------------------------------------------------------------------

test_every_decision_carries_the_bundles_hash if {
	regex.match(`^[0-9a-f]{64}$`, data.potka.bundle.hash)
	decide(base).bundle_hash == data.potka.bundle.hash
	decide({}).bundle_hash == data.potka.bundle.hash
}

test_a_bundle_without_its_hash_says_so_with_an_empty_one if {
	found := action.decision with input as base with data.potka.bundle as {}
	found.bundle_hash == ""
	other := action.decision with input as base with data.potka.bundle as {"hash": 7}
	other.bundle_hash == ""
}
