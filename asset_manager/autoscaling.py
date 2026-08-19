"""
autoscaling.py
--------------
One source of truth for the question "is this resource owned by an Auto Scaling
group?" — and therefore whether its coming and going is *churn*, not *drift*.

The cry-wolf problem this fixes: an Auto Scaling group launches and terminates
instances on its own. Every scale-out shows up in a live scan as a brand-new
resource the previous scan didn't have — which the drift report counts as
"added." That's a false alarm: the autoscaler owning an instance is the system
working as designed, not configuration drifting from Terraform.

The nice part is we need **no new API call and no new IAM permission** to know
this. EC2 stamps every ASG-launched instance with the reserved tag
`aws:autoscaling:groupName`, and we already read instance tags in the scanner.
So ownership is right there in the data we've had all along.

We suppress only the *existence* dimension — an instance the autoscaler
created appearing, and one it terminated vanishing. An actual attribute change
on a persistent instance — a security group opened, an AMI swapped — is still
real drift and still reported.
"""

from django.conf import settings

# EC2 auto-applies this reserved tag to every instance an ASG launches. Users
# can't set it themselves, so its presence is a reliable ownership signal.
ASG_TAG = 'aws:autoscaling:groupName'


def autoscaling_group_of(raw_data):
    """
    Name of the Auto Scaling group that owns this resource, or None.

    Prefers an explicit `autoscaling_group` key (set by the current scanner),
    then falls back to the `aws:autoscaling:groupName` tag — so assets scanned
    before this feature existed are classified correctly too.
    """
    if not raw_data:
        return None
    explicit = raw_data.get('autoscaling_group')
    if explicit:
        return explicit
    tags = raw_data.get('tags') or {}
    return tags.get(ASG_TAG) or None


def is_autoscaling_managed(raw_data):
    """True if an Auto Scaling group owns this resource."""
    return autoscaling_group_of(raw_data) is not None


def suppression_enabled():
    """Whether autoscaling churn is suppressed from drift (default on)."""
    return getattr(settings, 'DRIFT_SUPPRESS_AUTOSCALING', True)


def is_autoscaling_churn(raw_data):
    """
    True if a resource *appearing or disappearing* should be counted as
    autoscaling churn instead of drift.

    Both directions belong to the same existence dimension: a scale-out makes
    an instance show up, a scale-in makes one vanish, and neither is Terraform
    drifting. Callers apply this to the "added" and "removed" branches only —
    an attribute change on a persistent instance is never suppressed.
    """
    return suppression_enabled() and is_autoscaling_managed(raw_data)
