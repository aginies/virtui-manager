# Flatpak Makefile for VirtUI Manager
APP_ID = io.github.aginies.virtui-manager
MANIFEST = $(APP_ID).yml
BUILD_DIR = build-dir
REPO_DIR = repo

.PHONY: all build run install uninstall clean deps

all: build

# Install the required Flatpak runtimes and SDKs from Flathub
deps:
	flatpak install --user flathub org.freedesktop.Platform//23.08 org.freedesktop.Sdk//23.08

# Build the application locally
build:
	flatpak-builder --ccache --force-clean --user --install-deps-from=flathub $(BUILD_DIR) $(MANIFEST)

# Run the application from the build directory (for testing)
run:
	flatpak-builder --run $(BUILD_DIR) $(MANIFEST) virtui-manager

# Build and install the application to the local user account
install:
	flatpak-builder --user --install --force-clean $(BUILD_DIR) $(MANIFEST)

# Uninstall the application from the local user account
uninstall:
	flatpak uninstall --user $(APP_ID)

# Remove build artifacts and temporary directories
clean:
	rm -rf $(BUILD_DIR) $(REPO_DIR) .flatpak-builder
