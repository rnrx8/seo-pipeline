"""Route research records only to the editorial roles that use them."""
import copy


def review_requirements(requirements, role):
    # All user/editorial settings survive unchanged. Research evidence is not
    # needed to judge prose, internal consistency, or information presentation.
    result=copy.deepcopy(requirements)
    if role not in ('evidence','coverage','research'):
        result.pop('research_plan',None)
        result.pop('research_decisions',None)
    else:
        plan=result.get('research_plan')
        if isinstance(plan,dict):
            result['research_plan']={k:copy.deepcopy(v) for k,v in plan.items()
                                     if k not in ('policy_sha256','plan_retirements')}
        decisions=result.get('research_decisions')
        if isinstance(decisions,dict):
            # Preserve every decision, quotation, limitation and coverage gap.
            # Only runtime bookkeeping is removed, never fact/evidence fields.
            result['research_decisions']={k:copy.deepcopy(v) for k,v in decisions.items()
                                         if k not in ('policy_sha256','attempt','plan_sha256',
                                                      'sources_sha256','attempts_sha256','facts_sha256')}
    return result
