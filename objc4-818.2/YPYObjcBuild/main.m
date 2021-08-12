//
//  main.m
//  YPYObjcBuild
//

#import <Foundation/Foundation.h>
#import "Person.h"
#import <objc/runtime.h>
#import <malloc/malloc.h>
//https://note.youdao.com/ynoteshare1/index.html?id=0f2502577da6ac494931a5b127e21aaa&type=note

//https://opensource.apple.com/release/macos-112.html
//xcrun -sdk iphoneos clang -arch arm64 -rewrite-objc main.m -o main-arm64.cpp

//xcrun -sdk iphoneos clang -arch arm64 -rewrite-objc Person.m -o Person-arm64.cpp


int main(int argc, const char * argv[]) {
    @autoreleasepool {
//        NSQualityOfService
//        int i = 10;
//        int i1 = i--;
        
//        0x00007ffffffffff8ULL
        Person *p = [[Person alloc] init];
        p.name      = @"kkkk";
        p.nickName  = @"bbbbb";
        NSLog(@"%@ - %lu - %lu - %lu",p,sizeof(p),class_getInstanceSize([Person class]),malloc_size((__bridge const void *)(p)));
        
//        NSObject *p2 = [TModel alloc];
//        NSLog(@"%@ - %lu - %lu - %lu",p2,sizeof(p2),class_getInstanceSize([NSObject class]),malloc_size((__bridge const void *)(p2)));

        Class rnsuperClass = class_getSuperclass((Class)([NSObject class]->isa));


    }
    return 0;
}
