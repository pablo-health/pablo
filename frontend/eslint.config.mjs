import nextConfig from "eslint-config-next"

const rawErrorToConsole = {
  selector:
    "CallExpression[callee.object.name='console'][callee.property.name='error'] > Identifier.arguments",
  message:
    "Pass console.error() a derived value (e.g. errorCode(err)) instead of the raw error object.",
}

const inlineBrowserTimeZone = {
  selector:
    "MemberExpression[property.name='timeZone'] > CallExpression.object[callee.property.name='resolvedOptions']",
  message: "Use browserTimeZone() from @/lib/dates.",
}

// A poll that counts as activity keeps an unattended screen signed in past the
// idle sign-out. pollingOptions marks its requests so the API does not.
const bareRefetchInterval = {
  selector: "Property:matches([key.name='refetchInterval'], [key.value='refetchInterval'])",
  message:
    "Poll with pollingOptions() from @/lib/query/polling, so polled requests do not count as activity and keep an idle session signed in.",
}

const config = [
  ...nextConfig,
  // Mutation-testing sandboxes and reports (npm run test:mutation).
  { ignores: [".stryker-tmp/**", "reports/**"] },
  {
    rules: {
      "no-restricted-syntax": ["error", rawErrorToConsole, bareRefetchInterval],
    },
  },
  {
    files: ["src/lib/query/polling.ts"],
    rules: {
      "no-restricted-syntax": ["error", rawErrorToConsole],
    },
  },
  {
    files: ["**/*.ts", "**/*.tsx"],
    ignores: [
      "src/lib/**",
      "src/test/**",
      "**/__tests__/**",
      "**/*.test.ts",
      "**/*.test.tsx",
    ],
    rules: {
      "no-restricted-syntax": [
        "error",
        rawErrorToConsole,
        inlineBrowserTimeZone,
        bareRefetchInterval,
      ],
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "@/lib/mockData",
              message:
                "mockData pulls sample sessions and notes into the bundle. Load it with a dynamic import behind the relevant runtime check instead of a static import.",
            },
          ],
        },
      ],
    },
  },
  {
    files: [
      "app/(dashboard)/layout.tsx",
      "app/(dashboard)/dashboard/page.tsx",
    ],
    rules: {
      // These read mockUser behind a NODE_ENV check that the bundler
      // resolves at build time, so the branch (and the import) is
      // compiled out of production builds rather than shipped.
      "no-restricted-imports": "off",
    },
  },
]

export default config
