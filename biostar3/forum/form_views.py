__author__ = 'ialbert'

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.urlresolvers import reverse
from django.core.exceptions import ValidationError
from django.contrib.auth.decorators import login_required
from .models import Post, UserGroup, GroupSub, GroupPerm
from . import auth
from django.shortcuts import render, redirect
from django.contrib.sites.models import Site
from django.db.models import Q, F

import logging
from django.contrib.auth import get_user_model

logger = logging.getLogger('biostar')

# Get custom user model.
User = get_user_model()


def title_validator(text):
    "Validates form input for tags"
    text = text.strip()
    MIN_LEN, MIN_WORDS = 15, 3
    if not text:
        raise ValidationError('Please enter a title')

    if len(text) < MIN_LEN:
        raise ValidationError('Title is too short! Needs to have at least %s characters' % MIN_LEN)

    words = text.split()
    if len(words) < MIN_WORDS:
        raise ValidationError('Title too simple! Needs more than %s words please.' % MIN_WORDS)


def tag_validator(text):
    MAX_TAGS = 10
    parts = auth.tag_split(text)
    if len(parts) > MAX_TAGS:
        raise ValidationError('Too many tags! Have no more than %s tags please.' % MAX_TAGS)

    if len(parts) < 1:
        raise ValidationError('Please enter at least one tag!')


class ContentForm(forms.Form):
    """
    Edit or create content: answers, comments
    """
    # The is_toplevel field is used to distinguish between subclasses inside templates
    is_toplevel = False
    min_lenght = 5
    content = forms.CharField(widget=forms.Textarea,
                              min_length=min_lenght, max_length=settings.MAX_POST_SIZE,
                              initial="", required=True)


class PostForm(ContentForm):
    """
    Edit or create top level posts: question, news, forum posts,
    """
    is_toplevel = True
    min_lenght = 50

    title = forms.CharField(widget=forms.TextInput, initial='', max_length=200,
                            validators=[title_validator])
    tags = forms.CharField(max_length=100, initial='', validators=[tag_validator])
    type = forms.TypedChoiceField(coerce=int, choices=[
        (Post.QUESTION, "Question"), (Post.NEWS, "News"), (Post.FORUM, "Forum"), (Post.JOB, "Job Ad"),
    ])


def get_post(request, user, pk, edit_access_required=True):
    """
    Authenticates access to a post.
    """
    post = Post.objects.filter(pk=pk).select_related("group", "group__groupinfo").first()

    if not post:
        messages.error(request, "Post does not exist. Perhaps it has been deleted.")
        raise auth.AccessDenied()

    if not auth.read_access_post(user=user, post=post):
        messages.error(request, "This post may not be accessed by this user!")
        raise auth.AccessDenied()

    if edit_access_required and not auth.write_access_post(user, post):
        messages.error(request, "This post may not be edited by this user!")
        raise auth.AccessDenied()

    return post


def post_create(request, parent=None, post_type=None, action='', form_class=ContentForm):
    """
    This view creates nodes. Is not called directly from the web only through
    other functions that prefill parameters.
    """
    user, group = request.user, request.group
    template_name = "post_edit.html"

    if request.method == "GET":
        # This will render the initial form for the user.
        form = form_class()
        context = dict(form=form, action=action)
        return render(request, template_name, context)

    if request.method == "POST":
        # Data is being submitted
        form = form_class(request.POST)

        if not form.is_valid():
            # Form data came but not valid.
            context = dict(form=form, action=action)
            return render(request, template_name, context)

        # The form is valid create the post based on the form.
        if post_type is None:
            post = auth.create_toplevel_post(user=user, group=group, data=form.cleaned_data)
        else:
            content = form.cleaned_data['content']
            post = auth.create_content_post(content=content, post_type=post_type, user=user, parent=parent)

        return redirect(post.get_absolute_url())


@login_required
def create_toplevel_post(request):
    "A new toplevel post"
    action = reverse("new_post")
    return post_create(request=request, parent=None, post_type=None, action=action, form_class=PostForm)


@login_required
@auth.content_create
def create_answer(request, pk, parent=None):
    action = reverse("new_answer", kwargs=dict(pk=parent.id))
    return post_create(request=request, parent=parent, post_type=Post.ANSWER, action=action)


@login_required
@auth.content_create
def create_comment(request, pk, parent=None):
    action = reverse("new_comment", kwargs=dict(pk=parent.id))
    return post_create(request=request, parent=parent, post_type=Post.COMMENT, action=action)


@login_required
@auth.post_edit
def post_edit(request, pk, post=None, user=None):
    """
    This view updates posts.
    """

    template_name = "post_edit.html"
    action = reverse("post_edit", kwargs=dict(pk=post.id))

    if post.is_toplevel:
        # Different forms are chosen based on post type.
        # A form with title, type and tags.
        form_class = PostForm
        tags = ", ".join(post.tags.names())
        initial = dict(content=post.content, title=post.title, tags=tags, type=post.type)
    else:
        # Content only: answers and comments.
        form_class = ContentForm
        initial = dict(content=post.content)

    if request.method == "GET":
        # Get methods get the form and return.
        form = form_class(initial=initial)
        context = dict(form=form, action=action)
        return render(request, template_name, context)

    if request.method == "POST":
        # This is a form submission with incoming parameters.
        form = form_class(request.POST)

        if not form.is_valid():
            # Invalid form, bail out with error messaged.
            context = dict(form=form, action=action)
            return render(request, template_name, context)

        # The data is valid update the post and return the view to it.
        get = lambda word: form.cleaned_data.get(word, '')

        post.content = get('content')
        post.lastedit_user = user
        post.lastedit_date = auth.now()

        # Extra information to be saved for toplevel posts.
        if post.is_toplevel:
            post.title = get('title')
            post.type = get('type')
            tags = get('tags')
            tags = auth.tag_split(tags)
            # Must explicitly set the new tags.
            post.tags.set(*tags)

        post.save()

    return redirect(post.get_absolute_url())


def group_name_validator(text):
    if UserGroup.objects.filter(name=text).first():
        raise ValidationError('This group name already exists')


def group_domain_validator(text):
    if UserGroup.objects.filter(domain=text).first():
        raise ValidationError('This group domain already exists')


class GroupCreateForm(forms.Form):
    """
    Edit or create content: answers, comments
    """

    # The is_toplevel field is used to distinguish between subclasses inside templates
    name = forms.CharField(min_length=3, max_length=25, label="Group Name", validators=[group_name_validator])
    domain = forms.CharField(min_length=3, max_length=15, label="Subdomain", validators=[group_domain_validator])
    public = forms.BooleanField(initial=True, label="Public access", required=False)
    description = forms.CharField(widget=forms.Textarea, min_length=10, max_length=1000,
                                  required=True)

    logo = forms.FileField(required=False, label="Logo (image)")

    remove_logo = forms.BooleanField(initial=False, label="Remove logo if exists.", required=False)


class GroupEditForm(GroupCreateForm):
    name = forms.CharField(min_length=3, max_length=25, label="Group Name")
    domain = forms.CharField(min_length=3, max_length=15, label="Subdomain")


@login_required
@auth.group_create
def group_create(request, user=None):
    """
    The decorator will fill the user parameter.
    """
    title = "Create a group"
    template_name = "group_edit.html"
    action = reverse("group_create")

    if request.method == "GET":
        # Get methods get the form and return.
        form = GroupCreateForm()
        context = dict(form=form, action=action, title=title)
        return render(request, template_name, context)

    if request.method == "POST":
        # Process form submission.
        form = GroupCreateForm(request.POST, request.FILES)
        if not form.is_valid():
            # Form not valid. Return with an error message.
            context = dict(form=form, action=action, title=title)
            return render(request, template_name, context)

        # Create the group from the submission data.
        get = lambda x: form.cleaned_data.get(x, '')

        group = UserGroup.objects.create(
            name=get('name'),
            domain=get('domain'),
            public=get('public'),
            description=get('description'),
            owner=user,
            logo=request.FILES.get('logo'),
        )

        messages.info(request, "You have created the %s group." % group.name)
        return redirect(reverse("group_redirect", kwargs=dict(pk=group.id)))

    return redirect("group_list")


@login_required
@auth.group_edit
def group_edit(request, pk=None, group=None, user=None):
    """
    The decorator will fill the group and user parameters.
    """
    title = "Edit group"
    template_name = "group_edit.html"
    action = reverse("group_edit", kwargs=dict(pk=group.id))

    if request.method == "GET":
        # Get methods get the form and return.
        initial = dict(
            name=group.name, public=group.public, description=group.description,
            domain=group.domain,
        )
        form = GroupEditForm(initial=initial)
        context = dict(form=form, action=action, title=title)
        return render(request, template_name, context)

    if request.method == "POST":
        # Post request received.
        form = GroupEditForm(request.POST, request.FILES)

        if not form.is_valid():
            # Invalid form, return with errors.
            context = dict(form=form, action=action, title=title)
            return render(request, template_name, context)

        # Process the group edit.
        get = lambda x: form.cleaned_data.get(x, '')

        group.name = get('name')
        group.domain = get('domain')
        group.public = get('public')
        group.description = get('description')

        logo = request.FILES.get('logo')

        if logo or get("remove_logo"):
            # Incoming data. Remove old data.
            if group.logo:
                group.logo.delete()
            group.logo = logo

        group.save()

    return redirect(reverse("group_list"))


class GroupManager(forms.Form):
    MODERATE, ADMIN, REVOKE, KEEP = range(4)
    roles = [
        (KEEP, "Keep current"),
        (MODERATE, "Moderator"),
        (ADMIN, "Admin"),
        (REVOKE, "Revoke"),
    ]
    role = forms.TypedChoiceField(choices=roles, coerce=int)
    user_id = forms.IntegerField(widget=forms.HiddenInput)

@login_required
@auth.group_edit
def group_permission(request, pk=None, group=None, user=None):

    back = redirect("group_manage",pk=group.id)

    form = GroupManager(request.POST)

    if not form.is_valid():
        messages.error(request, "Invalid form submission")
        return back

    user_id = form.cleaned_data.get('user_id', '')
    role = form.cleaned_data.get('role', '')

    target = User.objects.filter(pk=user_id).first()

    if not target:
        messages.error(request, "User does not exists")
        return back

    if target == group.owner:
        messages.error(request, "Cannot modify group owner")
        return back

    perm = GroupPerm.objects.filter(user=target, usergroup=group).first()

    # Admin related changes.
    deny1 = (perm and perm.role == GroupPerm.ADMIN) and (user != group.owner)
    deny2 = (role == GroupManager.ADMIN) and (user != group.owner)

    if deny1 or deny2:
        messages.error(request, "Only group owners can modify Admin roles")
        return back

    create = GroupPerm.objects.create
    select = GroupPerm.objects.filter(user=target, usergroup=group)

    if role == GroupManager.REVOKE:
        select.delete()
        messages.info(request, "Revoked roles from %s" % target.name)
        return back

    if role == GroupManager.MODERATE:
        select.delete()
        perm = create(user=target, usergroup=group, role=GroupPerm.MODERATE)
        messages.info(request, "Addeded Moderator role to %s" % target.name)
        return back

    if role == GroupManager.ADMIN:
        select.delete()
        perm = create(user=target, usergroup=group, role=GroupPerm.ADMIN)
        messages.info(request, "Addeded Admin role to %s" % target.name)
        return back

    return back


@login_required
@auth.group_edit
def group_manage(request, pk=None, group=None, user=None):
    """
    Add moderators and admins to a group.
    This form works differently. On submission it stays on the same page to support the main use case.
    """
    template_name = "group_manage.html"

    # Get methods get the form and return.

    query = request.REQUEST.get("query", '')
    users = []

    if query:
        # Find users matching the query
        cond = Q(name__icontains=query) | Q(email__icontains=query)
        users = User.objects.filter(cond)[:10]
        for u in users:
            # Add a form field to each user.
            initial = dict(user_id=u.id, query=query)
            u.form = GroupManager(initial=initial)

    # Get all the group permissions
    perms = GroupPerm.objects.filter(usergroup=group).order_by("-user__last_login")
    context = dict(query=query, group=group, users=users, perms=perms)

    group.curr_role = GroupPerm.objects.filter(usergroup=group, user=user).first().get_role_display()

    return render(request, template_name, context)


class GroupSubscription(forms.Form):
    choices = settings.MESSAGE_CHOICES
    pref = forms.TypedChoiceField(choices=choices, coerce=int)


@login_required
@auth.group_access
def group_subscribe(request, pk, group=None, user=None):
    """
    The decorator will fill the group parameter.
    """
    template_name = "group_subscribe.html"

    if request.method == "GET":
        # Get methods get the form and return.
        sub = GroupSub.objects.filter(usergroup=group, user=user).first()
        initial = dict(pref=sub.pref) if sub else dict()
        form = GroupSubscription(initial=initial)
        context = dict(form=form, group=group)
        return render(request, template_name, context)

    if request.method == "POST":
        # Process form submission.
        form = GroupSubscription(request.POST)
        if not form.is_valid():
            # Form not valid. Return with an error message.
            context = dict(form=form, group=group)
            return render(request, template_name, context)

        pref = form.cleaned_data['pref']

        # Update group subscription.
        auth.groupsub_get_or_create(user=user, usergroup=group, pref=pref)

    return redirect("group_list")
