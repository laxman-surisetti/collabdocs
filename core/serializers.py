from rest_framework import serializers

from .models import (
    AuditLog, Comment, Document, DocumentVersion, Tag, User, Workspace, WorkspaceMember,
)


class UserSerializer(serializers.ModelSerializer):
    workspace_count = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'password', 'workspace_count']
        extra_kwargs = {'password': {'write_only': True}}

    def get_workspace_count(self, obj):
        # Populated by annotate() on the list endpoint; falls back to a query otherwise.
        if hasattr(obj, 'workspace_total'):
            return obj.workspace_total
        return obj.memberships.count()

    def validate_email(self, value):
        if not value:
            raise serializers.ValidationError('Email is required.')
        qs = User.objects.filter(email__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value

    def validate_password(self, value):
        if len(value) < 8:
            raise serializers.ValidationError('Password must be at least 8 characters.')
        return value

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class WorkspaceSerializer(serializers.ModelSerializer):
    owner_username = serializers.CharField(source='owner.username', read_only=True)
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = Workspace
        fields = ['id', 'name', 'owner', 'owner_username', 'is_active', 'created_at', 'member_count']
        read_only_fields = ['owner', 'created_at']

    def get_member_count(self, obj):
        if hasattr(obj, 'member_total'):
            return obj.member_total
        return obj.members.count()

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError('Workspace name cannot be blank.')
        return value.strip()


class WorkspaceMemberSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = WorkspaceMember
        fields = ['id', 'workspace', 'user', 'username', 'role']
        read_only_fields = ['workspace']
        # Uniqueness is enforced by the DB constraint and reported as 409 by the view.
        validators = []


class DocumentSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)
    tag_names = serializers.ListField(
        child=serializers.CharField(max_length=50), write_only=True, required=False,
        help_text='Names of existing tags to attach on creation.',
    )
    tags = serializers.SerializerMethodField()
    comment_count = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = [
            'id', 'workspace', 'title', 'content', 'status', 'created_by', 'created_by_username',
            'created_at', 'updated_at', 'tag_names', 'tags', 'comment_count',
        ]
        read_only_fields = ['created_by', 'created_at', 'updated_at']

    def get_tags(self, obj):
        return [t.name for t in obj.tags.all()]

    def get_comment_count(self, obj):
        if hasattr(obj, 'comment_total'):
            return obj.comment_total
        return obj.comments.count()

    def validate_title(self, value):
        if not value.strip():
            raise serializers.ValidationError('Title cannot be blank.')
        return value.strip()

    def validate(self, attrs):
        if self.instance and 'workspace' in attrs and attrs['workspace'] != self.instance.workspace:
            raise serializers.ValidationError({'workspace': 'A document cannot be moved to another workspace.'})
        workspace = attrs.get('workspace') or (self.instance.workspace if self.instance else None)
        if workspace is not None and not workspace.is_active:
            raise serializers.ValidationError({'workspace': 'This workspace is inactive.'})
        return attrs


class DocumentVersionSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)

    class Meta:
        model = DocumentVersion
        fields = ['id', 'document', 'version_number', 'content', 'created_by', 'created_by_username', 'created_at']


class CommentSerializer(serializers.ModelSerializer):
    author_username = serializers.CharField(source='author.username', read_only=True)

    class Meta:
        model = Comment
        fields = ['id', 'document', 'author', 'author_username', 'content', 'parent', 'created_at']
        read_only_fields = ['author', 'created_at']

    def validate_content(self, value):
        if not value.strip():
            raise serializers.ValidationError('Comment cannot be blank.')
        return value

    def validate(self, attrs):
        parent = attrs.get('parent')
        document = attrs.get('document') or (self.instance.document if self.instance else None)
        if parent is not None and document is not None and parent.document_id != document.id:
            raise serializers.ValidationError({'parent': 'Parent comment belongs to a different document.'})
        return attrs


class TagSerializer(serializers.ModelSerializer):
    document_count = serializers.SerializerMethodField()

    class Meta:
        model = Tag
        fields = ['id', 'name', 'document_count']
        extra_kwargs = {'name': {'validators': []}}

    def get_document_count(self, obj):
        if hasattr(obj, 'doc_total'):
            return obj.doc_total
        return obj.documents.count()


class AuditLogSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(source='actor.username', read_only=True)

    class Meta:
        model = AuditLog
        fields = ['id', 'actor', 'actor_username', 'action', 'model_name', 'object_id', 'created_at']
