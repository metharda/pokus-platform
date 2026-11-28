# What a caller may do with a tool: the one decision of the platform's policy.
#
# The executor asks about every call, whether its tool exists, is offered or
# is forbidden, and whether its arguments fit. The answer is an effect
# (allow, deny or require_approval), the ID of the rule that gave it, a
# reason, and the hash of this bundle.
#
# The rules are tried in a fixed order and the first that holds decides. The
# first of all asks that the input states every fact a rule reads, with its
# type and within its bounds: what the asker left out is not understood, and
# is never taken for something the caller did.
# Each denial holds when a fact the input must state is absent: a missing
# fact, or one of another type, is never read as permission. So a denial
# negates a rule by its name and nothing else: `not` before an expression
# that reads the input is undefined, not true, when what it reads is
# missing, and the rule would not hold. A tool's
# version, risk and categories are this bundle's own data (data.tools,
# compiled from the tool registry), never the input's word.
package potka.action

decision := object.union(verdict, {"bundle_hash": bundle})

# The bundle's own hash: its data, written when the bundle was made.
default bundle := ""

bundle := data.potka.bundle.hash if is_string(data.potka.bundle.hash)

# No rule held: an input the rules do not know.
default verdict := {"effect": "deny", "rule_id": "input_not_understood", "reason": "input_not_understood"}

# An input that does not state its facts.
verdict := answer("deny", "input_not_understood", "input_not_understood") if {
	not understood
} else := answer("deny", "tool_unknown", "tool_unknown") if {
	# 1. A tool that is unknown, forbidden or not offered, and a call that is not valid.
	not known
} else := answer("deny", "tool_forbidden", "tool_forbidden") if {
	tool.risk == "forbidden"
} else := answer("deny", "orchestrator_never_mutates", "orchestrator_never_mutates") if {
	# The orchestrator never mutates. A call of its own to a tool that changes something
	# is a fault of the platform, never a caller's behaviour: it is denied here, under
	# its own rule, whatever the tool is offered and whether its arguments fit.
	input.caller == "orchestrator"
	not read_only
} else := answer("deny", "tool_not_offered", "tool_not_offered") if {
	not offered
} else := answer("deny", "call_invalid", invalid_reason) if {
	not valid
} else := answer("deny", "caller_unknown", "caller_unknown") if {
	not caller_known
} else := answer("allow", "evidence_profile", "read_only_for_orchestrator") if {
	# 2. The orchestrator reads, for the evidence profile. What it may not do was denied
	# above, so no call of its own goes further than this.
	input.caller == "orchestrator"
	read_only
} else := answer("allow", "read_only", "read_only") if {
	# 3. A tool that changes nothing.
	read_only
} else := answer("deny", "diagnose_only", "diagnose_only_mode") if {
	# 4 and 5. The modes in which nothing is changed.
	input.mode == "diagnose_only"
} else := answer("deny", "recommend", "recommend_mode_not_executed") if {
	input.mode == "recommend"
} else := answer("deny", "mode_unknown", "mode_unknown") if {
	not acting_mode
} else := answer("deny", "diagnosis_required", "diagnosis_required_before_action") if {
	# 6 and 7. An action follows a diagnosis, and fits it.
	not diagnosed
} else := answer("deny", "diagnosis_inconsistent", "action_inconsistent_with_diagnosis") if {
	not diagnosis_fits
} else := answer("deny", "repeat_budget", "same_action_repeated_too_often") if {
	# 8. The budgets.
	not repeats_within_budget
} else := answer("deny", "mutating_budget", "mutating_budget_exceeded") if {
	not mutating_within_budget
} else := answer("require_approval", "high_risk", "high_risk") if {
	# 9 and 10. What a person decides.
	tool.risk == "high"
} else := answer("require_approval", "medium_risk", "medium_risk") if {
	tool.risk == "medium"
} else := answer("allow", "low_risk_confident", "low_risk_confident") if {
	# 11. A low risk, by the two confidences and the mode.
	tool.risk == "low"
	confident
} else := answer("require_approval", "low_risk_unsure", "low_risk_below_thresholds") if {
	tool.risk == "low"
	input.mode == "human_in_loop"
} else := answer("deny", "low_risk_unsure_autonomous", "low_risk_below_thresholds") if {
	tool.risk == "low"
	input.mode == "autonomous_low_risk"
}

answer(effect, rule_id, reason) := {"effect": effect, "rule_id": rule_id, "reason": reason}

# The input states every fact a rule reads, with its type and within its bounds.
understood if {
	is_string(input.caller)
	is_string(input.mode)
	is_string(input.tool.name)
	is_boolean(input.tool.known)
	is_boolean(input.tool.offered)
	is_boolean(input.call.valid)
	version_stated
	diagnosis_stated
	confidence_stated
	is_count(input.counters.mutating_calls)
	is_count(input.counters.same_action_repeats)
	is_count(input.budgets.max_mutating_calls)
	is_count(input.budgets.max_same_action_repeats)
	is_share(input.thresholds.min_action_confidence)
	is_share(input.thresholds.min_diagnosis_confidence)
}

# A valid call names the version of the tool its arguments were checked for.
version_stated if input.call.valid == false

version_stated if is_number(input.tool.version)

# A diagnosis is whole, or there is none.
diagnosis_stated if input.diagnosis.present == false

diagnosis_stated if {
	input.diagnosis.present == true
	is_string(input.diagnosis.label)
	is_share(input.diagnosis.confidence)
}

# A valid call of a tool that changes something says how sure it is.
confidence_stated if {
	not changing
}

confidence_stated if is_share(input.meta.action_confidence)

changing if {
	input.call.valid == true
	tool.mutating == true
}

# A count: a whole number, zero or more. Its type first: a comparison orders a value of
# another type, it does not refuse it.
is_count(value) if {
	is_number(value)
	value >= 0
	floor(value) == value
}

# A share: a number from zero to one, its type first as well.
is_share(value) if {
	is_number(value)
	value >= 0
	value <= 1
}

# The tool as this bundle knows it.
tool := data.tools[input.tool.name]

# The registry has the tool, and it is the one the executor means: a call that
# names a version names this bundle's.
known if {
	input.tool.known == true
	tool.version == object.get(input.tool, "version", tool.version)
}

offered if input.tool.offered == true

caller_known if input.caller in {"agent", "orchestrator"}

# The modes in which an agent may change something.
acting_mode if input.mode in {"human_in_loop", "autonomous_low_risk"}

# The call's arguments passed their checks, and the call says which tool it checked them for.
valid if {
	input.call.valid == true
	is_number(input.tool.version)
}

default invalid_reason := "invalid_call"

invalid_reason := sprintf("invalid_call:%s", [input.call.error]) if {
	is_string(input.call.error)
	regex.match(`^[a-z][a-z0-9_]{0,63}$`, input.call.error)
}

read_only if tool.mutating == false

diagnosed if {
	input.diagnosis.present == true
	is_string(input.diagnosis.label)
}

diagnosis_fits if input.diagnosis.label in tool.applicable_categories

repeats_within_budget if {
	is_number(input.counters.same_action_repeats)
	is_number(input.budgets.max_same_action_repeats)
	input.counters.same_action_repeats <= input.budgets.max_same_action_repeats
}

mutating_within_budget if {
	is_number(input.counters.mutating_calls)
	is_number(input.budgets.max_mutating_calls)
	input.counters.mutating_calls < input.budgets.max_mutating_calls
}

confident if {
	is_number(input.meta.action_confidence)
	is_number(input.diagnosis.confidence)
	is_number(input.thresholds.min_action_confidence)
	is_number(input.thresholds.min_diagnosis_confidence)
	input.meta.action_confidence >= input.thresholds.min_action_confidence
	input.diagnosis.confidence >= input.thresholds.min_diagnosis_confidence
}
