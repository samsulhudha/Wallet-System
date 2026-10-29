from rest_framework import generics, permissions, status
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiResponse
from .serializers import UserRegistrationSerializer, UserProfileSerializer


@extend_schema(
    tags=['Accounts'],
    summary='Register a new customer account',
    responses={
        201: UserProfileSerializer,
        400: OpenApiResponse(description='Validation error'),
    }
)
class RegisterView(generics.CreateAPIView):
    serializer_class = UserRegistrationSerializer
    permission_classes = [permissions.AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        output_serializer = UserProfileSerializer(user)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)


@extend_schema(
    tags=['Accounts'],
    summary='Retrieve current authenticated user profile',
    responses={200: UserProfileSerializer}
)
class ProfileView(generics.RetrieveAPIView):
    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user

