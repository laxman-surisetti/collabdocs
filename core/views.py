import uuid

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import (
    AuditLog, Comment, Document, DocumentVersion, Tag, User, Workspace, WorkspaceMember,
)
from .serializers import (
    AuditLogSerializer, CommentSerializer, DocumentSerializer, DocumentVersionSerializer,
    TagSerializer, UserSerializer, WorkspaceMemberSerializer, WorkspaceSerializer,
)

Role = WorkspaceMember.Role


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = 'Conflict with existing data.'
    default_code = 'conflict'


# ----------------------------------------------------------------- helpers

def member_workspace_ids(user):
    """IDs only, so values_list is enough."""
    return WorkspaceMember.objects.filter(user=user).values_list('workspace_id', flat=True)


def get_role(user, workspace):
    return (
        WorkspaceMember.objects.filter(workspace=workspace, user=user)
        .values_list('role', flat=True)
        .first()
    )


def require_role(user, workspace, allowed):
    role = get_role(user, workspace)
    if role is None:
        raise NotFound('Workspace not found.')
    if role not in allowed:
        raise PermissionDenied(f'This action requires one of these roles: {", ".join(allowed)}.')
    return role


def date_param(request, key):
    raw = request.query_params.get(key)
    if raw is None:
        return None
    try:
        value = parse_date(raw)
    except ValueError:
        value = None
    if value is None:
        raise ValidationError({key: 'Use a valid date in YYYY-MM-DD format.'})
    return value


def uuid_param(request, key):
    raw = request.query_params.get(key)
    if raw is None:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise ValidationError({key: 'Must be a valid UUID.'})


def uuid_list_param(request, key):
    raw = request.query_params.get(key)
    if not raw:
        return None
    try:
        return [uuid.UUID(part.strip()) for part in raw.split(',')]
    except ValueError:
        raise ValidationError({key: 'Must be comma-separated UUIDs.'})


def csv_param(request, key):
    raw = request.query_params.get(key)
    if not raw:
        return None
    return [part.strip() for part in raw.split(',') if part.strip()]


def bool_param(request, key):
    raw = request.query_params.get(key)
    if raw is None:
        return None
    if raw.lower() in ('true', '1'):
        return True
    if raw.lower() in ('false', '0'):
        return False
    raise ValidationError({key: 'Must be true or false.'})


def apply_date_range(qs, request, field='created_at'):
    after = date_param(request, 'created_after')
    before = date_param(request, 'created_before')
    if after:
        qs = qs.filter(**{f'{field}__date__gte': after})
    if before:
        qs = qs.filter(**{f'{field}__date__lte': before})
    return qs


# ------------------------------------------------------------------- users

class UserViewSet(viewsets.ModelViewSet):
    serializer_class = UserSerializer
    http_method_names = ['get', 'post', 'head', 'options']

    def get_permissions(self):
        if self.action == 'create':
            return [AllowAny()]
        return super().get_permissions()

    def get_queryset(self):
        qs = User.objects.annotate(workspace_total=Count('memberships')).order_by('username')
        if self.action == 'list':
            username = self.request.query_params.get('username')
            email = self.request.query_params.get('email')
            ids = uuid_list_param(self.request, 'ids')
            if username:
                qs = qs.filter(username__icontains=username)
            if email:
                qs = qs.filter(email__icontains=email)
            if ids:
                qs = qs.filter(id__in=ids)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                user = serializer.save()
        except IntegrityError:
            raise Conflict('A user with this username already exists.')
        return Response(self.get_serializer(user).data, status=status.HTTP_201_CREATED)


# -------------------------------------------------------------- workspaces

class WorkspaceViewSet(viewsets.ModelViewSet):
    serializer_class = WorkspaceSerializer

    def get_queryset(self):
        qs = (
            Workspace.objects.filter(id__in=member_workspace_ids(self.request.user))
            .select_related('owner')
            .annotate(member_total=Count('members'))
            .order_by('-created_at')
        )
        if self.action == 'list':
            name = self.request.query_params.get('name')
            is_active = bool_param(self.request, 'is_active')
            ids = uuid_list_param(self.request, 'ids')
            if name:
                qs = qs.filter(name__icontains=name)
            if is_active is not None:
                qs = qs.filter(is_active=is_active)
            if ids:
                qs = qs.filter(id__in=ids)
            qs = apply_date_range(qs, self.request)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            workspace = serializer.save(owner=request.user)
            WorkspaceMember.objects.create(workspace=workspace, user=request.user, role=Role.ADMIN)
            AuditLog.objects.create(
                actor=request.user,
                action=AuditLog.Action.CREATED,
                model_name='Workspace',
                object_id=workspace.id,
            )
        workspace = self.get_queryset().get(pk=workspace.pk)
        return Response(self.get_serializer(workspace).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        require_role(self.request.user, serializer.instance, [Role.ADMIN])
        serializer.save()

    def perform_destroy(self, instance):
        require_role(self.request.user, instance, [Role.ADMIN])
        instance.delete()

    @action(detail=True, methods=['get', 'post'], url_path='members')
    def members(self, request, pk=None):
        workspace = self.get_object()

        if request.method == 'GET':
            qs = WorkspaceMember.objects.filter(workspace=workspace).select_related('user', 'workspace')
            roles = csv_param(request, 'role')
            if roles:
                qs = qs.filter(role__in=roles)
            return Response(WorkspaceMemberSerializer(qs, many=True).data)

        require_role(request.user, workspace, [Role.ADMIN])
        user_id = request.data.get('user')
        role = request.data.get('role', Role.VIEWER)
        if not user_id:
            raise ValidationError({'user': 'This field is required.'})
        if role not in Role.values:
            raise ValidationError({'role': f'Must be one of: {", ".join(Role.values)}.'})
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            raise NotFound('User not found.')
        except (ValueError, DjangoValidationError):
            raise ValidationError({'user': 'Must be a valid UUID.'})

        try:
            with transaction.atomic():
                member = WorkspaceMember.objects.create(workspace=workspace, user=user, role=role)
        except IntegrityError:
            raise Conflict('This user is already a member of the workspace.')
        member = WorkspaceMember.objects.select_related('user', 'workspace').get(pk=member.pk)
        return Response(WorkspaceMemberSerializer(member).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='stats')
    def stats(self, request, pk=None):
        workspace = self.get_object()
        totals = Workspace.objects.filter(pk=workspace.pk).aggregate(
            member_count=Count('members', distinct=True),
            document_count=Count('documents', distinct=True),
            comment_count=Count('documents__comments', distinct=True),
            version_count=Count('documents__versions', distinct=True),
        )
        by_status = {
            row['status']: row['total']
            for row in Document.objects.filter(workspace=workspace)
            .values('status').annotate(total=Count('id'))
        }
        by_role = {
            row['role']: row['total']
            for row in WorkspaceMember.objects.filter(workspace=workspace)
            .values('role').annotate(total=Count('id'))
        }
        return Response({
            'workspace': workspace.id,
            **totals,
            'documents_by_status': by_status,
            'members_by_role': by_role,
        })


# --------------------------------------------------------------- documents

class DocumentViewSet(viewsets.ModelViewSet):
    serializer_class = DocumentSerializer

    def get_queryset(self):
        qs = (
            Document.objects.filter(workspace_id__in=member_workspace_ids(self.request.user))
            .select_related('workspace', 'created_by')
            .prefetch_related('tags')
            .annotate(comment_total=Count('comments', distinct=True))
            .order_by('-created_at')
        )
        if self.action == 'list':
            params = self.request.query_params
            workspace = uuid_param(self.request, 'workspace')
            statuses = csv_param(self.request, 'status__in')
            title = params.get('title')
            search = params.get('q')
            tag = params.get('tag')
            created_by = uuid_param(self.request, 'created_by')
            if workspace:
                qs = qs.filter(workspace_id=workspace)
            if statuses:
                qs = qs.filter(status__in=statuses)
            if title:
                qs = qs.filter(title__icontains=title)
            if search:
                qs = qs.filter(Q(title__icontains=search) | Q(content__icontains=search))
            if tag:
                qs = qs.filter(tags__name=tag)
            if created_by:
                qs = qs.filter(created_by_id=created_by)
            qs = apply_date_range(qs, self.request)
        return qs

    def _add_version(self, document, user):
        number = document.versions.count() + 1
        return DocumentVersion.objects.create(
            document=document, version_number=number, content=document.content, created_by=user,
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        tag_names = data.pop('tag_names', [])
        require_role(request.user, data['workspace'], [Role.ADMIN, Role.EDITOR])

        try:
            with transaction.atomic():
                # The post_save signal writes the AuditLog inside this same transaction.
                document = Document.objects.create(created_by=request.user, **data)
                self._add_version(document, request.user)
                for name in tag_names:
                    try:
                        tag = Tag.objects.get(name=name)
                    except Tag.DoesNotExist:
                        raise NotFound(f"Tag '{name}' does not exist; nothing was saved.")
                    tag.documents.add(document)
        except IntegrityError:
            raise Conflict('Document could not be saved because of a data conflict.')

        document = self.get_queryset().get(pk=document.pk)
        return Response(self.get_serializer(document).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        document = self.get_object()
        require_role(request.user, document.workspace, [Role.ADMIN, Role.EDITOR])
        serializer = self.get_serializer(document, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.validated_data.pop('tag_names', None)

        try:
            with transaction.atomic():
                document = serializer.save()
                self._add_version(document, request.user)
        except IntegrityError:
            raise Conflict('Document could not be saved because of a data conflict.')

        document = self.get_queryset().get(pk=document.pk)
        return Response(self.get_serializer(document).data)

    def perform_destroy(self, instance):
        require_role(self.request.user, instance.workspace, [Role.ADMIN])
        instance.delete()

    @action(detail=True, methods=['get'], url_path='versions')
    def versions(self, request, pk=None):
        document = self.get_object()
        qs = DocumentVersion.objects.filter(document=document).select_related('created_by', 'document')
        return Response(DocumentVersionSerializer(qs, many=True).data)

    @action(detail=True, methods=['get', 'post'], url_path='tags')
    def tags(self, request, pk=None):
        document = self.get_object()

        if request.method == 'GET':
            qs = Tag.objects.filter(documents=document).annotate(doc_total=Count('documents'))
            return Response(TagSerializer(qs, many=True).data)

        require_role(request.user, document.workspace, [Role.ADMIN, Role.EDITOR])
        name = str(request.data.get('name', '')).strip()
        if not name:
            raise ValidationError({'name': 'This field is required.'})
        if len(name) > 50:
            raise ValidationError({'name': 'Ensure this field has no more than 50 characters.'})

        with transaction.atomic():
            tag, _ = Tag.objects.get_or_create(name=name)
            already = tag.documents.filter(pk=document.pk).exists()
            if not already:
                tag.documents.add(document)
        tag = Tag.objects.annotate(doc_total=Count('documents')).get(pk=tag.pk)
        return Response(
            TagSerializer(tag).data,
            status=status.HTTP_200_OK if already else status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------- comments

class CommentViewSet(viewsets.ModelViewSet):
    serializer_class = CommentSerializer

    def get_queryset(self):
        qs = (
            Comment.objects.filter(document__workspace_id__in=member_workspace_ids(self.request.user))
            .select_related('author', 'document', 'parent')
            .order_by('created_at')
        )
        if self.action == 'list':
            document = uuid_param(self.request, 'document')
            author = uuid_param(self.request, 'author')
            search = self.request.query_params.get('q')
            top_level = bool_param(self.request, 'top_level')
            if document:
                qs = qs.filter(document_id=document)
            if author:
                qs = qs.filter(author_id=author)
            if search:
                qs = qs.filter(content__icontains=search)
            if top_level is not None:
                qs = qs.filter(parent__isnull=top_level)
            qs = apply_date_range(qs, self.request)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document = serializer.validated_data['document']
        if get_role(request.user, document.workspace) is None:
            raise NotFound('Document not found.')
        with transaction.atomic():
            comment = serializer.save(author=request.user)
        comment = self.get_queryset().get(pk=comment.pk)
        return Response(self.get_serializer(comment).data, status=status.HTTP_201_CREATED)

    def _require_author_or_admin(self, comment):
        if comment.author_id == self.request.user.id:
            return
        require_role(self.request.user, comment.document.workspace, [Role.ADMIN])

    def perform_update(self, serializer):
        self._require_author_or_admin(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._require_author_or_admin(instance)
        instance.delete()


# -------------------------------------------------------------------- tags

class TagViewSet(viewsets.ModelViewSet):
    serializer_class = TagSerializer
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        qs = Tag.objects.annotate(doc_total=Count('documents')).order_by('name')
        if self.action == 'list':
            name = self.request.query_params.get('name')
            names = csv_param(self.request, 'name__in')
            if name:
                qs = qs.filter(name__icontains=name)
            if names:
                qs = qs.filter(name__in=names)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                tag = serializer.save()
        except IntegrityError:
            raise Conflict('A tag with this name already exists.')
        return Response(self.get_serializer(tag).data, status=status.HTTP_201_CREATED)


# -------------------------------------------------------------- audit logs

class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditLogSerializer

    def get_queryset(self):
        qs = AuditLog.objects.select_related('actor')
        if not self.request.user.is_staff:
            qs = qs.filter(actor=self.request.user)
        if self.action == 'list':
            params = self.request.query_params
            model_name = params.get('model_name')
            actions = csv_param(self.request, 'action__in')
            object_ids = uuid_list_param(self.request, 'object_id__in')
            actor = uuid_param(self.request, 'actor')
            if model_name:
                qs = qs.filter(model_name__iexact=model_name)
            if actions:
                qs = qs.filter(action__in=actions)
            if object_ids:
                qs = qs.filter(object_id__in=object_ids)
            if actor and self.request.user.is_staff:
                qs = qs.filter(actor_id=actor)
            qs = apply_date_range(qs, self.request)
        return qs
